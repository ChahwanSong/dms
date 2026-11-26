# DMS 변경 이력 (최신순 — 2026-10-21 테스트베드 분리 이후)

dms-k8s 라인의 **완료 기록**이다. 각 항목에는 무엇을 만들었는지, 어떤 실증을 통과했는지, 구현 중 잡은 결함과 교훈이 있다.

- **지금 시스템이 어떻게 동작하는가**는 여기가 아니라 [`ARCHITECTURE.md`](ARCHITECTURE.md)
  와 코드의 「왜」 주석을 봐라. 이 파일은 "무엇을·언제·왜 그렇게 지었나"의 역사다.
- **왜 그렇게 설계했나**의 원문 근거는 동결된 설계문서 [`docs/history/specs/`](history/specs/)에
  있다. 설계마다 지금도 유효한지, 무엇이 대체했는지는 [`history/README.md`](history/README.md) 색인에 있다.
  구현 플랜(TDD 스크립트)은 일회성이라 트리에서 지웠고 git 이력에만 남는다(복구 방법도 같은 README).
- **남은 일**은 [`BACKLOG.md`](BACKLOG.md).

## 읽는 법

- **정렬**: 위가 최신이다(완료 날짜 역순, 같은 날은 대체로 출하 역순). 새 항목은 「상세 기록」 맨 위에 더한다.
  제목 끝 괄호는 (완료·실증 날짜, 그때 출하한 태그 또는 실증 이름 T*)다.
- **당시 기준**: 본문의 경로·절 번호·태그·수치는 그 항목을 쓴 시점의 것이고, 고치지 않는다. 지금 동작은
  ARCHITECTURE 와 코드를 본다. 옛 deploy/README 절 번호(§13·§2d-5·§2e-6·§2f-8·§2g-7 등)는
  [`../deploy/README.md`](../deploy/README.md) 「부록 Z. 옛 절 번호 대응」에서 지금 위치를 찾는다.
- **태그 계열** — 이름이 겹치므로 시기로 가른다(근거: 오버레이 bump 커밋 — `git log -- deploy/overlays/testbed-k8s/kustomization.yaml`):

| 계열 | 시기·클러스터 | 어디에 기록됐나 |
|---|---|---|
| ssc `dN` (d1~d194) | 2026-08-04 ~ 10-21: 그때의 단일 테스트베드(지금의 ssc 클러스터). 10-22 분리 뒤에는 dms-ssc 라인이 이어 간다 | 이관본 두 편. 이 파일에는 대조·이식으로만 나온다(「ssc d185」, 「dms-ssc 이식」의 d190~d193, d194) |
| k8s `kN` (k0~k4) | 2026-10-21 ~ 10-26: k8s 테스트베드(dmsk8s)의 첫 계열. k0 = ssc d184·d179·d183 이미지 시드 복사 | 이 파일 「테스트베드 분리」(k0·k1) ~ 「dms-k8s RBAC·PV」(k3·k4) |
| k8s `dN` (d1~) | 2026-10-26 ~ (커밋 c3c1209a 에서 태그 관례 kN → dN): k8s 테스트베드 | 이 파일. 지금 라이브 태그는 `deploy/overlays/testbed-k8s/kustomization.yaml` 의 `newTag` |

> **번호 겹침 주의**: k8s `dN` 은 ssc `dN` 과 번호가 겹친다. 예를 들어 ssc d23 은 2026-08-09 무렵의 태그(옛 빠른 인덱스의
> 「Phase 1~3 … d23~」)이고, k8s d23 은 2026-11-05 에 출하한 태그(아래 인덱스의 11-05 행 「라이브 기능 전수 점검」)다. 이 파일에서
> d179~d194(세 자리)는 ssc 계열이고, 「ssc」 표시가 없는 한두 자리 dN(d1~)은 k8s 계열이다(지금 라이브 태그는 위 표의 `newTag`).
> 이관본 두 편의 `dN` 은 모두 ssc 계열이다.

## 옛 기록 (이관본 — 동결)

2026-11-06 에 2026-10-21 이전 기록을 `docs/history/` 로 옮겼다. 본문은 원본 줄을 바이트 그대로 옮겼고, 머리 3줄에 원본 줄 범위를 적었다.

| 파일 | 기간·태그 | 내용 |
|---|---|---|
| [`history/changelog-2026-09-to-10-21.md`](history/changelog-2026-09-to-10-21.md) | 2026-09-07 ~ 10-21, ssc d119~d184 | ssc 테스트베드 시절(main·dms-ssc 와 공유한 시기) 기록 56항목: 웹 인증 하드닝·제어면 root·base 사이트 중립화·Knox 메일·root 실행 opt-in·작업(요청) 삭제·배치 sync/rm 자동 확인·사용량 분석 등 |
| [`history/changelog-2026-08-slices.md`](history/changelog-2026-08-slices.md) | 2026-08, ssc ~d81 | 옛 「빠른 인덱스」(Phase 1~3·슬라이스 1~30), 상세 기록 20항목(슬라이스 16~31·33~35 + dscan 1b93d54 정합), 착수 전 계획·후보 블록 8개 |

**기록이 없는 옛 작업**(이관본 어디에도 항목이 없다 — git 이력으로 확인해 2026-11-06 보충):

| 무엇 | 날짜·태그 | 근거 커밋 |
|---|---|---|
| 슬라이스 32 — 배치 생성 개편(4스텝 위저드·입력 4방식·배치 priority/node_count·전체 재실행 `:rescan`) | 2026-08-13, ssc d47 | 플랜 690588f0 · 배포 7733b928 |
| 슬라이스 36 — 컨트롤 상태 「현재 상태」 보강(적용값 배지·영향 요약·변경 이력 `GET /api/admin/control-state/history`) + sync·rm 충돌 키에서 옵션 지문 제거 | 2026-08-18, ssc d84·d85 | 35c34f21 · 166bb5f6 |
| 슬라이스 37 — 「작업 제출」 → 「단일 작업」(운영자 scan 흡수·정책 기본 캡션), 포탈의 「내 스캔 경로」·「scan 실행」 메뉴와 `/scan-paths`·`/admin/scan` 라우트·화면 제거(백엔드 `/api/user/scan-paths` 는 남음) | 2026-08-18, ssc d87 | 74bb3ae9 · 배포 f2284ad8 |
| 그 밖의 2026-08-13 ~ 09-07 배포(ssc d43~d118 가운데 위 표와 슬라이스 33~35(d73~d75·d81)를 뺀 것) | — | `git log --since=2026-08-13 --until=2026-09-08` |

## 인덱스 (이 파일 — 최신순)

제목은 아래 「상세 기록」의 제목 그대로다(파일 안 검색으로 찾는다). 태그 열의 「—」은 제목에 태그가 없는 항목(인프라·문서·테스트
위주)이고, 「(본문)」은 본문에서 확인한 태그다.

| 날짜 | 태그·실증 | 제목 |
|---|---|---|
| 11-27 | d49~d51 | 단일·배치 등록 상단 스테퍼 — 단일 작업 4단계·배치 생성 5단계(초안 1)·단계 게이트 순수 함수·Enter 는 다음 단계·제출 더블클릭 그물 |
| 11-27 | d49~d51 | sync 진행률 — 작업 상세 진행 중 실행·미리보기 행의 dsync·nsync 진행 칸 + mpirun 출력 스트리밍·출력 상한(큰 디렉터리 효율화) |
| 11-26 | d48 | 포탈 본문 열 최대 1440·가운데(A안) — 넓은 창에서 사이드바 오른쪽 영역 가운데, 화면 기준선은 하나 그대로 |
| 11-25 | d46·d47 | 마운트 현황 표 압축 레이아웃 — 열 폭 고정·「보고」 열 합침·열 머리 툴팁·100열 열 거르기·고정 칸 그늘·화면 높이 래퍼 |
| 11-25 | d44·d45 | stale 노드 기록 정리(기록 삭제·자동 정리·새 이름 힌트) + 노드 상세 「관리 디렉토리」 열 + 빌드·릴리스·컨트롤 상태·아티팩트 경로를 관리 메뉴로 |
| 11-23 | d42·d43 | 빌드 파드 메모리 상한 1Gi → 2Gi + 프런트 빌드 Node 힙 1024MB 고정 |
| 11-23 | d40·d41 | API 파드 자체 아티팩트 경로 점검·시작 게이트·「base 접근 불가」 503 사유 + 관리 화면 API/컨트롤러 분리 + 마운트 현황 DMS 파드 꼬리표(dms-ssc 4add57bf 이식) |
| 11-23 | d38(d37 건너뜀) | 포탈 이미지 빌드의 프런트 타입 검사 heap OOM — 이미지 빌드는 앱 코드만 타입 검사 |
| 11-23 | d38 | 스토리지 > 마운트 현황(노드 × 스토리지 매트릭스) + 에이전트 공용 디렉터리 덮는 마운트 보고 |
| 11-19 | d35·d36 | 배치 단위 삭제 상한 기본 50000 + 청크(백그라운드) 삭제 + 삭제 화면 상한 표시·「삭제 불가」 |
| 11-18 | d32·d33 | 테스트베드 사내 AD SSO 환경 구축·실증 + 실증 결함 3건 수정 |
| 11-18 | — | 사내 AD SSO 로그인(OIDC Authorization Code + PKCE) |
| 11-17 | — | pull-probe 노드 여러 대 지정 수정 — 마스터 3대 사전 pull 에서 apiserver 가 DaemonSet 생성을 거부하던 것 |
| 11-17 | — | 설치기 S11 에이전트 DaemonSet 대기를 노드 수 비례로 — 6노드 사이트의 정상 롤링(약 600초)을 고정 180초가 끊던 것 + 시간 초과 뒤 재실행이 설정 재시작을 되풀이하던 것 |
| 11-17 | — | 설치기 S11 설정 재시작 누락 수정 — 포탈 릴리스 뒤 태그를 맞추는 S11 이 api·controller 를 다시 띄우지 않던 결함 |
| 11-16 | d31 | 스케줄 배치 회차 보존 기본 5 → 10 d31 |
| 11-16 | d29·d30(본문) | 스케줄 회차 최근 5회차 보존·배치 단위 삭제 상한 5000 d29 |
| 11-16 | d28 | 포탈 개선 — 사용량 전체 목록·최신 scan 차트 · 배치 대상 스토리지 열·큰 배치 상세 쪽 넘김·검색 · 배치 스케줄러 d28 |
| 11-16 | d27 | 입구 — Envoy Gateway 폐기, 클러스터의 Traefik 하나로 교체(테스트베드 영구 적용·실증) d27 |
| 11-14 | — | 입구 — 리스너 포트(GATEWAY_LISTEN_PORT)·PROXY off 모드·마스터 hostPort 충돌 감지(OA: Traefik 이 호스트 443, Avi PROXY v2 미지원) |
| 11-13 | — | OA 사이트(RKE2) 설치 — Gateway API 하한 v1.5.0·묶음 dry-run 오류 줄·control-plane 라벨 값(CONTROL_PLANE_LABEL_VALUE) |
| 11-11 | d26 | 포탈 전수조사 — 버그 92건 수정(high 0 · medium 12 · low 80) d26 |
| 11-11 | d25 | 포탈 k8s 연동 — 클러스터를 개요에서 별도 「클러스터」 탭으로 분리 d25 |
| 11-11 | d24 | 지운 k8s 클러스터의 동기화 이력 — 보존 정리 결함·호출 노드 카드에서 제외 d24 |
| 11-06 | — | 문서 전면 정리 — 프로덕션 기준 배포 런북·테스트베드 분리·CHANGELOG 이관·ARCHITECTURE 심볼 앵커·base 중립화 |
| 11-05 | d23 | 라이브 기능 전수 점검(2026-11-04) + 확정 결함 5건 수정 d23 |
| 11-04 | — | 옛 dmsk8s 레지스트리 pkg-01:5001 삭제 — testbed IaC 은퇴 목록·노드 신뢰 정리·DMS 헬퍼·문서 |
| 11-04 | — | 테스트베드 정식 구축 — 입구 영구 컷오버(호스트 edge haproxy = Avi VS-443 흉내 → Envoy cp1:443 PROXY v2, MetalLB 유지)·pkg-01 journald 상한 |
| 11-03 | — | 테스트베드 워커 EL9(T-SE) — k8s-w1·w2 를 Rocky Linux 9.6(SELinux permissive)으로 재구성, SELinux 프로브·firewalld·EL9 고유 실측 |
| 11-03 | T9 | ⑦ 설치기 — 단계 설치기(S1~S13)·감지 상태 기계·SELinux·pull·마스터 프로브·인터넷 노드 push·S5 lock 대조·MetalLB/ingress 게이트 삭제 |
| 11-03 | T8 | ⑥ 입구(Envoy Gateway) — Gateway API v1.6.1 standard·EG v1.9.3 애드온 층·DMS Gateway/HTTPRoute/정책/EnvoyProxy·prod 의 Ingress·MetalLB 삭제 |
| 11-03 | T6·T7·T6' | ⑤ DB(CNPG) — CloudNativePG 1.30.1 애드온 층·정적 hostPath PV 풀·Cluster dms-pg·pg_dump·경로 프로브·prod 제어면 마스터 배치 |
| 11-03 | d19 | ④ 클러스터 내 레지스트리 — Distribution v3.1.2 플랫폼 층·렌더러 일반화·images.lock·가드 |
| 11-03 | d18 | ③ 공용 디렉터리(SHARED_DIR) — 아티팩트 경로 한 원천(`<SHARED_DIR>/dms/artifacts`)·hostPath 가드(exit 5)·스토리지 등록 가드·S0 초기화·S7 동작 프로브 |
| 11-03 | d17 | ② 로그인·식별 — 아이디 기준 상한·원 IP 는 기록 전용·인증번호 6자리·대기형 보호 장치·관리자 구제·보안 이벤트 화면 |
| 11-03 | d17 | ① B3 — ref 기록이 끊긴 틱의 잡 객체를 이어 받거나 회수한다(409 AlreadyExists 를 잡 실패로 접지 않음) |
| 10-30 | d16 | 사용량 분석 「소속별」 탭(2단계) — 권한 사용자·요청자·namespace·PV 이름·StorageClass·클러스터별 실 사용량 순위 |
| 10-29 | d15 | 사용량 분석 「경로별」 — PV 이름 열 + 경로·PV 이름 검색(일부 단어) |
| 10-29 | d14 | 사용량 분석 하위 메뉴 — 경로별 · 스토리지별 + 경로 상세 시트 + 합계 줄 |
| 10-29 | d12 | 사용량 분석 필터 — 사용자별(권한 사용자·요청자)·PV 이름·namespace·클러스터·StorageClass·구분 |
| 10-28 | d11(본문) | 경로 봉쇄 개행 구멍(§8 ②) 셸 수정 |
| 10-28 | d10(본문) | k8s 연동 호출 노드 카드 — 해결된 실패 구분·클러스터별 연결·보조 연결 확인 |
| 10-28 | d9 | dms-ssc 이식 — mpifileutils 7a3189c · d190 · d191(경로 봉쇄 통일) · d192 · d193 |
| 10-26~27 | d6 | BACKLOG 결정 묶음 구현 — 직접 경로 결과 소유·rm 경로 겹침 잠금·결과 파일 자동 만료·배치 삭제 상한 2000·dscan 카운터 + 보류 기능 5건 + 결함 3건 |
| 10-26 | d5 | 스토리지 기본 소유자(uid:gid) — PV 목적지 sync 결과 소유 |
| 10-26 | d4 | LDAP 코드 삭제·정리(dms-k8s) — k8s 신원 모드 전용 |
| 10-26 | d3 | d3 라이브 실증 — PV 소유자 chown·setuid·namespaceAdmin·scan·경고·숫자 아이디·배치 PV·멀티 클러스터 |
| 10-26 | — | PostgreSQL 로 k8s 테스트 실행 + 자리표시자 리터럴 결함 |
| 10-26 | d3 | dms-k8s 결정 항목 반영 — namespaceAdmin 맵핑·동기화 실패는 경고만·배치 PV 검색·scan 특권 운영자·숫자 아이디·PV 버튼 |
| 10-26 | d3 | PV 목적지 sync 의 결과 소유 = 목적지 PV 루트 소유자(자동 chown) |
| 10-26 | d1 → d2 | nsync 목적지 링크 추종 수정 반영 — root nsync 의 PV 목적지 허용 |
| 10-25~26 | k3·k4(본문) | dms-k8s RBAC·PV — 인벤토리·관리자 화면·제출·권한·스토리지·PV 봉쇄 |
| 10-25 | k2 | 전체 작업: 요청자 부분 일치 + 배치 이름·대상 경로 검색 + 열 폭(요청 ↓ · 배치 ↑ · 시각 = 글자 폭) |
| 10-22 | k1 · ssc d185 | nsync 미리보기·실행 파드가 반대편 스토리지를 마운트하던 문제 |
| 10-21~22 | k0 → k1 | 테스트베드 분리 — k8s 테스트베드(LDAP 없음) 배포 자산·가드 컨텍스트 대조 |

---

## 상세 기록 (최신순)

### ✨ 단일·배치 등록 상단 스테퍼 — 단일 작업 4단계·배치 생성 5단계(초안 1)·단계 게이트 순수 함수·Enter 는 다음 단계·제출 더블클릭 그물 — **완료·실증**(2026-11-27, d49 → d51)

사용자(2026-11-26): 「단일작업 / 배치작업 등록 시 화면 한번에 너무 많은 글자 및 정보가 떠. Step별로 페이지가 넘어가면서 진행될수 있도록 개선하고
싶어. 최신 frotnend UI 및 디자인을 적용해서 초안을 몇가지 작성해서 나한테 보여줘봐.」 → 눌러 볼 수 있는 초안 셋(① 상단 스테퍼 + 단계 카드 + 하단
고정 바 ② 왼쪽 세로 단계 + 오른쪽 실시간 요약 ③ 한 화면에 한 질문 — artifact 「단계별 등록 초안」 https://claude.ai/artifact/S1X5J3T2hyuuw8Z7JFoZ72,
①은 입력 단계 글자 수가 지금의 3분의 1 이하) → 「단계별 등록 - 첫번째 안으로 진행해.」 → 설계 최종판(비판 33건을 코드로 검증·반영) → 공용 기반 →
단일·배치 레인 병렬 → 통합 → 리뷰(위저드 관점 should 1·nit 4) 반영. **백엔드·제출 바디·사유 코드 무변경**(그리는 방식만 바뀐다).

- **2026-10-06 이력과의 관계**: 10-06 에 같은 두 화면의 4스텝 위저드(max-w-xl/2xl 카드)를 걷고 「한 장 시트 + 오른쪽 제출 요약」(SubmitLayout)으로
  갔다(이관본 「단일 작업·배치 생성 화면 단일 페이지화(시트 + 오른쪽 제출 요약)」, ssc d159·d160 — 사용자 요청 「데스크탑에서 모바일처럼 폭이 좁다」).
  그때의 실패 넷 — 1440 화면의 오른쪽 절반이 빔, 한 스텝이 드롭다운 하나뿐, 전체를 보려면 다음·이전을 오감, 확인 스텝이 JSON 한 줄 — 을 이렇게
  피했다: 본문 열 전체 폭(같은 날 A안의 1440 열 — 화면이 max-w·mx-auto 를 두지 않는다), 단계마다 의미 있는 묶음(배치 스토리지는 라디오 카드 묶음,
  고를 것이 없는 단계는 건너뜀 — 연산이 하나뿐이면 ①을 완료로 두고 ②에서 시작), 스테퍼 아래 값(방문한 단계의 고른 값), 사람이 읽는 검토 카드
  (SummaryList·OptionTokens 재사용). 그래서 `SubmitLayout`·`SummaryPanel`·`FormSection` 은 지웠다(`components/form/SubmitLayout.tsx` 머리 주석이 이력,
  남는 FieldRow·SummaryList·SummaryRow·OptionTokens·SubmitHints 는 계약 주석 그대로).
- **정한 것**(기본값 — 다시 묻지 않음, 설계 0-1): 입력 칸 **Enter = 그 단계가 유효하면 다음 단계, 제출은 절대 안 함**(「경로 칸 Enter 로 제출 안 됨」
  계약 유지 — 검색 칸 `data-wizard-enter="stay"`·datalist 칸은 단계를 옮기지 않고, 체크박스·라디오는 막기만); **제출 더블클릭 = 두 번째 클릭 무시**;
  연산이 하나뿐인 사용자(사이트 기본 `DMS_USER_ALLOWED_OPERATIONS=sync`)는 ② 대상에서 시작; 일반 사용자도 4단계 유지 + ②에 「옵션 기본값으로 검토」
  지름길(옵션을 거친 뒤에는 「검토로 건너뛰기」); 단건 rm 에 새 확인 체크를 더하지 않음(「되돌릴 수 없음」 경고는 검토 바 제출 버튼 위); rm 검토의
  제출은 빨간 솔리드(새 Button 변형 `destructive` — 단일·배치); 연산 고르기는 카드형 라디오(접근 이름 「연산」·라디오 이름 sync·scan·rm 그대로).
- **공용 골격** `components/form/Wizard.tsx`(useWizard + WizardStepper·WizardStepCard·WizardActionBar·WizardShell — 규칙 전문은 머리 주석, 규약 요약은
  `frontend/README.md` 「규약」): 게이트 합집합 불변식(단계 게이트의 합 = 옛 제출 게이트), 도달 규칙(현재 앞의 막힘은 잠금을 만들지 않고 「다음」·
  「이전」은 현재 게이트만 — 비동기 재조회 실패가 뒤 단계 사용자를 끌어내리지 않고 빨간 점 + 「n단계에서 고치기」), Enter 규칙 ①~⑦(한글 조합·포털
  안·행 칸 = 같은 열 다음 행), 「다음」은 aria-disabled(막힌 채 누르면 이유 줄 강조 + 첫 aria-invalid/`data-wizard-nudge` 칸 포커스)·제출은 disabled,
  **「다음」과 제출이 바의 같은 `<Button>`(key 고정) + 바가 제출 onClick 의 `event.detail > 1` 을 거름**, 모든 버튼 `type="button"`, `?step=` 거울(사용자
  이동 push·맞춤 replace·뒤로/앞으로(POP)에만 반응·처음 렌더는 min(URL, 첫 막힘)), 단계 전환 시 h2 포커스, 단계 카드 aria-label 금지, 바 z-index 금지.
  단계 부품 `wizardParts.tsx`(MoreText — 요약은 늘·전문은 「자세히」, OptionToggle — 겉모양만 스위치·역할은 체크박스, OperationCards, ReviewCard —
  「수정: 제목」, SubSection), `optionRules` 요약 상수(HELP_SUMMARY — 전문 상수 옆, 정보 손실 0).
- **단일 작업** `/jobs/new` — 작업 종류 → 대상 → 옵션(관리자는 옵션·실행 설정) → 검토·제출. `k8sBody()`·잎 계산 무변경(바디 `toEqual` 단언 한 글자도
  안 바꿈). 게이트 `features/jobs/submitGates.ts`(옛 blocked·hints 의 식·문장·순서 그대로 + 단계 태그 — storageOwnerBad 는 특권 운영자면 ③(chown
  우회로 — 2026-11-11 결함 재발 방지, 고급 옵션 강제 펼침), 그 밖은 ②). `?pv=` 딥링크는 ②에서. 제출 방어 셋(바의 detail 가드·`handleSubmit` 단계 가드
  `current !== "review"`·type=button). PV 검색어는 화면 최상위로 끌어올려 단계를 옮겨도 남는다(LocationField `searchField`).
- **배치 생성** `/admin/batches/new` — 작업 종류 → 스토리지(라디오 카드 — 묶음마다 다른 radio name) → 항목 → 옵션·실행 제어 → 이름·검토. `buildBody()`
  무변경, 게이트 `features/batches/batchGates.ts`. 「스토리지를 CSV 로 정하기」 탈출구(스토리지 단계 게이트가 생겨도 CSV 의 storage 열로 채우는 흐름 —
  e2e 14 — 가 산다), 「PV 에서 경로 찾기」는 오른쪽 시트(넣어도 열어 두고 넣은 뒤 lastFocus 를 비움 — 「두 번째 넣기는 새 행」 계약), 방향어 「아래
  실행 제어」 → 「④ 옵션·실행 제어」(문구 수정 둘). 행 삭제 뒤 포커스는 같은 번호 행 → 이전 행 → 「행 추가」.
- **설계와 다르게 간 것**(실 Chrome 근거): 스테퍼의 비현재 단계 이름은 lg(1024)부터·값은 xl(1280)부터(설계 md·lg — 768 에서 다섯 이름이 「작.」·「스」로
  잘렸다, 접근 이름은 sr-only 로 남는다); 더블클릭 가드를 화면이 아니라 바가 쥔다(화면이 빠뜨려도 막힌다 — 그물은 Wizard.test 의 노드 동일성 시험);
  768 바 상태 줄 꺾임은 바의 flex-wrap + 상태 칸 기준 폭 12rem(sm:basis-48)으로 풀고 지름길은 설계대로 <sm 에서만 숨긴다(리뷰 반영 전엔 lg 아래에서
  숨겼다); 「검토로 돌아가기」는 바가 그린다; 검토 단계(마지막)에는 완료 체크·빨간 점을 붙이지 않는다.
- **리뷰 반영**(위저드 몫 5건 + 진행률과 같은 커밋 b1eddc3c): 정책 캡션의 거부 경고(정책 목록 조회 실패·관련 정책 행 비활성·행 부재)는 「자세히」에
  접지 않고 펼친다(`optionRules.policyCaptionAlert` — 단일 ③·배치 ④ 공용; 접히면 관리자가 비활성 정책을 모른 채 제출할 수 있었다), 지름길 <sm 만
  숨김, 검토 이유(SubmitHints)·제출 오류를 버튼 줄 위로, rm 에서 PV 안 경로가 비면 빨강 없는 `data-wizard-nudge` 표지(막힌 「다음」이 그 칸으로
  포커스), 배치 CSV 카드가 스토리지가 채워진 뒤에도 「비어 있는 …」을 말하던 모순(`csvStorageActive`).
- **테스트**: vitest 신규 `Wizard.test.tsx` 27(스테퍼 aria·도달 규칙·Enter 전 규칙·더블클릭 같은 노드·URL push/POP/비 POP 무시/처음 렌더 보정·type
  불변식·nudge 문서 순서)·`wizardParts.test.tsx` 9·`submitGates.test.ts`(잎 조합 **786,432** 전수 — 합집합·막힌 단계의 바 이유·옛 hints 일치)·
  `batchGates.test.ts`(**12,288** 전수), Button destructive. SubmitJob(59)·SubmitJob.k8s(38)·BatchCreate(91)·BatchCreate.k8s(8) 를 단계 흐름으로 다시
  썼다 — 옮긴 설명은 toBeVisible/not.toBeVisible 로(접혀도 DOM 에 남아 getByText 는 초록이라 의미 변화를 못 잡는다). 변이 확인: Enter preventDefault·
  POP 검사·제출 key·바 detail 가드·destructive·단계 가드·lastFocus 비우기·검색어 끌어올림을 각각 빼면 빨강. e2e: 03-layout 의 제출 화면 흐름을
  `helpers/layoutSingle.ts`·`layoutBatch.ts` 로 옮겨 단계마다 L1·L3·L4, 768 spot check(② 지름길이 보이고 바 상태 줄 ≤ 2줄 — flex-wrap 을 되돌리면
  폭 36px·높이 203px 로 빨강), 375 Tab 포커스가 하단 바에 안 가림, 시트를 열면 바가 덮개 아래·닫으면 연 버튼으로 포커스; 04 는 단계 흐름 + 마지막 앞
  「다음」 dblclick → 검토에 머묾(실 브라우저 더블클릭 그물); 07·10·11·12·14 단계 흐름(카드 label 클릭 + toBeChecked). e2e 도우미 `e2e/helpers/wizard.ts`
  (이름 매칭은 exact 또는 앞 고정 정규식 — 막힌 「다음」은 aria-disabled 라 `click({ force: true })`). 실 Chrome(공용 기반 임시 하네스 1280·1024·768·375,
  배치 1440·1024·768·390)에서 문서 가로 넘침 0.
- 게이트는 아래 진행률 항목과 같다(두 작업을 한 번에 쟀다).
- **라이브 실증(d49, 2026-11-27 — 실 Chrome)**: 새 위저드로 실제 sync 를 제출해 Succeeded(50,105 항목·1.19 GiB) — 스테퍼 aria-current
  ①→④·URL `?step=`, ② 빈 상태 「다음」 aria-disabled + 이유 줄, 막힌 「다음」을 눌러도 머묾, 저장된 payload 가 기존 바디와 같다. 화면 점검 19건
  통과: 1440·390 단계 이동 가로 넘침 0·콘솔 오류 0, 단일 rm ④ 제출·배치 rm ⑤ 「배치 생성」 빨강, **390 터치 연타**(touchscreen.tap·locator.tap
  두 번 <100ms) → 검토에 머물고 POST 0(BACKLOG 「터치 연속 탭」 해소 — 시계 잠금 불필요), 배치 rm 자동 실행 동의 전 「다음」 비활성, ② 경로 칸
  Enter(유효 → 다음·무효 → 머묾, 제출 없음). d50 후속: 배치 rm ④ 막힌 「다음」 → 화면 밖 동의 체크로 포커스·스크롤(`data-wizard-nudge` — 1440×900·
  390×844 에서 하단 바에 안 가림, 실증). 제출 바디 함수 `k8sBody`·`buildBody` 는 기준(a13ebab9)과 바이트 동일(직접 대조).

### ✨ sync 진행률 — 작업 상세 진행 중 실행·미리보기 행의 dsync·nsync 진행 칸 + mpirun 출력 스트리밍·출력 상한(큰 디렉터리 효율화) — **완료·실증**(2026-11-27, d49 → d51)

사용자(2026-11-26): 「Sync 작업을 수행할 때, 현재 얼마나 진행됬는지 보고싶어. 일단 dms의 mpifileutils에서 dsync, nsync 수행 시 진행과정이 로그로
찍히는지 파악해줘. 실증 및 소스코드도 보고 차악해서 어떤 방식으로 (초단위? 파일 단위) 찍히는지 파악해봐.」 → 조사(포크 소스 7a3189c + 테스트베드
실측, 시험 데이터 100,105 항목·1.381 GiB): dsync 는 **시간 간격**(기본 10초 — 복사 단계 간격 128개 중 122개가 정확히 10.0초)으로 단계별 진행 줄을
찍고, nsync 는 **배치가 끝날 때마다** 한 줄(`Progress P% batch i/N …`, 남은 시간 없음 — 기본 `--batch-files` 100만이면 100만 항목 미만은 배치 1개라
끝에 한 줄)이다. 그런데 **DMS 에서는 실행 중에 볼 수 없었다** — 러너가 `subprocess.run(capture_output)` 으로 출력을 끝까지 메모리에 모았다가 한 번에
써서, 22분 52초짜리 dsync 동안 stdout.log 가 404 였고 세 파일이 도구가 끝난 같은 시각에 한꺼번에 생겼다. 결정(같은 날): 「Sync 포탈 진행률 -
권장안으로 진행하고, nsync 는 배치 단위로 찍는것 오케이야 그대로 진행해. 검증할때만 배치사이즈를 줄여서 테스트해봐. 잡 목록/배치화면에서 진행률을
모아 볼 필요는 없고, 포탈에서 각 작업 항목에 들어갔을때만진행률 보여지게 해줘. 디렉토리가 엄청 크면,진행률 로그 파일이 너무 커질수 있으니
효율화하는 방안도 검토해서 구현에 반영해.」 → 설계 2판(비판 27건 대조·반영) → **계약 먼저 한 커밋**(93158201) → 러너+API·포탈 병렬 → 독립 리뷰
4관점(러너 동시성·보안 불변식·위저드·진행률 화면 — 진행률 몫 should 4·nit 7, must 0) 전부 반영.

- **정한 것**(기본값 — 다시 묻지 않음): 권장안 = 러너가 도구 줄을 실시간으로 읽어 최신 상태 하나만 담은 `<phase>/progress.json` 을 덮어쓰고 포탈이
  읽는다. **nsync 포크는 고치지 않는다**(배치 단위 진행 그대로 — 배치가 1개면 화면이 「nsync 는 배치가 끝날 때만 진행을 알립니다 — 이 작업은
  배치가 1개라 끝날 때 한 번 표시됩니다」를 말하고 막대를 만들지 않는다). 미리보기(dry-run)도 같은 장치. **남은 시간은 보이지 않는다** — 도구의
  「secs left」 는 평균 속도라 크기가 섞이면 크게 틀렸다(실측: 큰 파일이 먼저면 20초에 73% 뒤 작은 파일 10만 개에 21분, 그동안 8~103초를 오감;
  반대 순서는 45% 에서 「1088 secs left」 뒤 3.9초에 끝) — progress.json 에도 담지 않는다. 진행 값은 **표시 전용**(확인 게이트·지문·통계·사유 코드에
  쓰지 않음), DB·컨트롤러 저장 없음, **새 사유 코드 없음**, 러너 SIGTERM 처리기 없음(PID 1 신호 의미 유지).
- **러너(잡 이미지 `dms-mpifileutils`)** — `dms_job_runner/stream.py`(신규, 불변 T1~T7 은 모듈 docstring): Popen 스트리밍, daemon 리더 2개가
  원시 fd 를 `os.read` 하고 `io.IncrementalNewlineDecoder(utf-8 replace, translate=True)` 로 디코드(예전 텍스트 모드와 같은 결과 — 청크 경계의
  멀티바이트·`\r\n` 포함, 실제 자식 프로세스·무작위 읽기 크기 시험), 쓰기 스레드 하나가 모든 파일 I/O(phase dirfd + O_CREAT|O_EXCL|O_NOFOLLOW 새
  inode, progress.json 은 임시 파일 → rename — 독자는 반쪽 JSON 을 보지 않는다), 진행 슬롯을 로그 큐보다 먼저 씀, 모든 소비자·쓰기 예외는 그 출력만
  끄고 삼킴(파이프 배수는 멈추지 않는다), launcher stdout/stderr(파드 로그)에 아무것도 찍지 않음, 스레드가 남으면 flush 뒤 `os._exit`.
  stdout.log·stderr.log 는 이제 **mpirun 시작 때 만들고 도는 동안 이어 쓴다**. `progress.py`(신규): 진행 줄 정규식 표(줄 전체 fullmatch, 오류 줄·4 KiB
  초과 줄 즉시 탈락, 첫 단어 분기 — 오류 폭주에서도 줄당 µs, dsync·nsync 표 분리, nsync 실행/미리보기 줄 분리) + `Tracker`(스냅숏 v1). `runner.run_job`
  은 `run_tool`·`open_sink` 를 주입받아 본문이 파일시스템을 직접 만지지 않고(「모든 I/O 는 주입」 유지 — 기존 시험 22곳은 compat 경로 그대로),
  `workers` 스냅숏은 ALLOWED_TOOLS 검사 직후, `start` 는 mpirun 직전, 끝은 `ended:true`. `parsers.py` 에 넘침 모드 줄 단위 도우미(기존 함수 무변경).
- **API(`dms` 이미지 — api·controller 공용, controller 코드 변경 없음)** — `GET /api/user/jobs/{job_id}/progress?phase=execution|preview`
  (`routes_artifacts.get_job_progress`): 기존 열람 사슬 `_owned_job` → `_reject_expired` → `_owner_uid` → `_probe` → `open_artifact_fd` 그대로, 파일
  없음·사슬 실패·모양 틀림은 모두 200 `{phase, available:false}` 한 몸통이고 그 실패 경로에서만 BaseProbe 로 503 `artifact_base_unavailable`(불변식 16),
  사슬 안 HTTPException 에도 `Cache-Control: no-store` 를 덧붙여 **모든 응답이 no-store**, `age_seconds` = 서버 시각 − mtime. 중립 모듈
  `dms/job_progress.py`(신규) 의 `sanitize` 는 계약 서술자의 type·범위·required·const 만 보고 틀린 필드만 뺀다(required 가 틀리거나 16 KiB 초과·비 UTF-8·
  깨진 JSON·깊은 중첩이면 None — 예외 없음·멱등).
- **포탈** — `useArtifacts.useJobProgress`(5초, `available:false`·오류면 15초, 숨긴 탭 정지), `progressModel.ts`(순수 — 막대 우선순위 nsync 배치 % > dsync
  전체 항목(`--batch-files` 블록) > dsync 단계 %(복사는 바이트·배치 안) > 소스 탐색 추정(99% 상한) > 없음; null ≠ 0; 신선도 = 서버 나이 + 받은 뒤 흐른
  시간, 90초 넘으면 정체 경고(workers·ended 는 끔); available:false 는 단정하지 않음 — 60초 전 「진행 정보를 기다리는 중…」, 뒤는 Pending·이전 잡 이미지를
  함께 말하는 중립 문구), `JobProgress.tsx`(「진행 · 단계」 + 갱신 → 막대 role=progressbar·기준 문구·HelpTip → 사실 조각 → 안내, aria-live 없음),
  `JobStages` StepRow 는 **진행 중 ∧ 실행·미리보기 행 ∧ dsync·nsync ∧ 그 단계 ref 있음**일 때만(`showsProgress`). 잡 목록·배치 상세는 /progress 를
  부르지 않는다(시험 0건 고정 — 사용자 결정). `StageResults` 는 summary `output_truncated` 면 「도구 출력이 너무 커서 일부 수치(…)는 모릅니다」 한 줄.
- **계약** — `frontend/src/lib/progressContract.json`(스키마 v1 필드 서술자·phase 18개(공통·dsync·nsync)·불변식(since_line_s ≤ phase_since_s ≤
  elapsed_s, since_line_s ⇔ lines > 0)·금지 키 `line`·금지 글자 left/remaining·예 스냅숏 9개(실측 로그를 전이 규칙으로 흘려 계산, 129~378 B)·예 응답·
  `poll_ms` 5000/15000)이 러너 Tracker 재생·API 서술자 대조·포탈 progressModel 시험의 **단일 원천**이다(`tests/test_job_progress_contract.py` 가 형식과
  `types.ts` 의 `ProgressSnapshot`·`JobProgressResponse` 를 대조). summary.json 계약은 그대로이고 `output_truncated` 만 새 선택 키다.
- **큰 디렉터리 효율화**(사용자 요구 — 원문은 ARCHITECTURE §6 「러너 출력은 스트리밍·상한」):
  - **progress.json 은 고정 크기·덮어쓰기** — 이력·표본 배열 없이 최신 스냅숏 하나(계약 예 129 B(workers)·322 B(dsync 복사 + 전체 블록)·378 B(nsync 배치), 러너
    직렬화 상한 4 KiB — 넘으면 그 회차를 건너뜀, API 읽기 상한 16 KiB). 쓰기는 바뀐 것이 있으면 3초 스로틀, 없어도 30초 심장박동, workers·start·끝은
    즉시 — 시간당 최대 1,320회, dsync 는 진행 줄이 10초 간격이라 약 360회/시간(22분 실측 로그 재생 약 140회). **항목이 1억이어도 파일 크기·쓰기 횟수는
    같다.** 도구 원문 줄은 담지 않는다(남은 시간 원문이 칩으로 새지 않게, stdout.log 와 중복).
  - **stdout.log 64 + 16 MiB**(예전 무제한 — 실패 100만 ≈ 250 MB, 1,000만 ≈ 2.5 GB 였다) — 앞 64 MiB(실행 중 실시간) + 끝 16 MiB(메모리 링, 끝날 때
    붙임) + 사이 `DMS_JR_LOG_GAP reason=size omitted_lines=… omitted_bytes=… head=67108864 tail=16777216` 한 줄(UTF-8 바이트, 최대 약 80 MiB — 다운로드
    상한 256 MiB 안, 뷰는 끝 256 KiB 라 꼬리를 보인다). stderr.log 는 8 + 2 MiB. 진행 줄은 솎지 않는다(dsync 10초 간격 약 100 B — 1주 6 MB, nsync
    배치당 1줄 — 10억 항목도 270 KB). 폭주의 원인은 항목별 오류·WARN 줄이다.
  - **파서 입력 64 MiB(넘침 모드)** — 결합 메모리(문자 수 × 최대 문자 폭) 64 MiB 이하면 지금과 같은 문자열(파서 무변경 — compat·스트리밍 두 경로의
    summary 동일을 tool_detail 표본 15개로 시험). 넘치면 앞 64 MiB(완결 줄) + 넘친 구간의 요약 후보 줄 링(앞 4 MiB + 끝 4 MiB — 끝 요약은 늘 남는다) +
    스트리밍 정확 카운터 + 출력 전체의 첫 실패 표본 10개, 위치에 기대는 값은 모름(null) + summary `output_truncated: true`(미리보기·실행 공통). 예외:
    dsync 실행에서 정확한 copy_failed 가 0 이고 링이 줄을 밀어내지 않았으면 files·copied_items 는 최종 블록 값 그대로(리뷰 should — WARN 줄 폭주로
    성공한 큰 sync 의 「반영 항목」이 모름이 되던 회귀). launcher 메모리가 출력 크기만큼 늘던 것(수 GB·OOM 가능)이 상한 안으로 들어왔다.
  - 쓰기 큐 상한 16 MiB(넘치면 블록을 버리고 `DMS_JR_LOG_GAP reason=backlog` 로 센다 — 느린 CephFS 가 도구를 멈추지 않는다).
- **설계와 다르게 간 것**: 계약의 수 타입을 int·float 둘로 통일(설계 스케치의 num 없음), 예 오류 응답을 `{status, body}` 목록으로(404 가 둘), nsync
  미리보기의 Phase 5 줄은 실측(M4)이 `planner dryrun complete` 라 정규식을 `planner(?:\+execute| dryrun) complete` 로 넓혀 미리보기도 finishing 을 거친다.
- **리뷰 반영**(진행률 몫 11건): 넘침 성공 dsync 의 files 보존(위), `sanitize` 의 실수 필드 거대 정수(309자리 넘는 정수가 `math.isfinite` 에서
  OverflowError → GET progress 500·no-store 없음 — 정수는 범위만 비교 + 본문 예외를 None 으로 접는 2차 방어), echo 링을 넘침 전환 때 머리 끝 줄로 먼저
  채움(실패 꼬리 발췌가 몇 줄로 줄지 않게), 리더가 정수 fd 대신 파이프 파일 객체를 쥠(막힌 리더의 fd 가 반환 뒤 GC 로 닫혀 재사용되던 구멍), 로그가
  열리기 전에 꺼낸 첫 블록을 버리지 않음, `artifact_files` 소유자 장벽 docstring 을 실제에 맞게(summary.json·조기 종료 stderr.log 는 아직 `write_text`),
  dsync 단계 % 의 도구 반올림 100(99.5% 부터)을 `step.done` 이 아니면 99 로, `age_seconds` 부재를 0 으로 치지 않음, 정체 문턱 ≥ 러너 심장박동 × 3 을
  러너 소스와 시험으로 묶음(계약 키는 BACKLOG), 범위가 드러나는 이름(「지금까지 계획 …」·「이번 배치 …」), nsync 단일 배치 0/1 은 막대 없음.
- **테스트**: 백엔드 신규 `test_job_runner_stream.py` 122(디코드 동치 R1, 넘침·상한·GAP·backlog, fail-soft 주입, daemon·`os._exit` 서브프로세스,
  쓰기 경합·리더 fd 생존·echo 꼬리, main 배선), `test_job_runner_progress.py` 83(정규식 표·전이·스로틀·심장박동·계약 예 스냅숏 9개를 같은 로그·같은
  시각에서 재현 — 실측 로그 픽스처 `tests/fixtures/progress/` 6개), `test_api_job_progress.py` 83(소유·admin·공유 토큰, 422·410·404(job/artifact)·503(빈
  로컬 base·hung 단락), 심링크·하드링크·FIFO·디렉터리·상한 초과·비 UTF-8·깨진 JSON·깊은 중첩·v 2·bool v·거대 정수 → 같은 몸통, 모든 응답 no-store,
  서술자 == 계약), `test_job_progress_contract.py` 26(형식 + 변조 15건 음성 확인). 보강: `test_job_runner_runner.py`(main 배선)·`test_job_runner_tool_detail.py`
  (compat·스트리밍 동일). 프런트 신규 `progressModel.test.ts` 40(계약 예 스냅숏·응답 전부)·`JobProgress.test.tsx` 7, 보강 useArtifacts(가짜 타이머
  5초/15초·숨김·회복)·JobStages·StageResults·JobsList·BatchDetail(/progress 0건).
- **게이트**(두 작업 합친 마지막 통합 시점, 2026-11-27 실측): 백엔드 전수 **6657 passed · 19 skipped**(600초 — 기준 6321 에서 +336: 진행률 새 시험 파일 넷 314 + 러너 기존 시험 보강 22), vitest **2398 passed · 136 파일**(기준 2254·130 에서 +144·+6), `tsc -b` 오류 0, `tsc -p tsconfig.e2e.json --noEmit` 오류 0, `npm run build:image` 성공 · `dist/index.html` 외부 URL(http·https) 0건, e2e **43 passed · 19 spec**(3.6분 — 수 그대로, 새 단언은 기존 spec 안). 커밋은 a13ebab9 위 13개(계약 93158201 → 위저드 ed7fe322·fe87a767·219e8870·798480ab·46caffe3·7bfa2be3·5b7263a1 → 진행률 f405873b·abd43c8c·92f7a9f1 → 리뷰 반영 b1eddc3c·ded1a791) + 이 문서 커밋.
- **라이브 실증(d49 → d50 → d51, 2026-11-27 — 테스트베드 dmsk8s, 시험 데이터 small 50,000×4 KiB + big 512 MiB×2; nsync 갱을 위해 사용자 허락
  「다른 리소스를 줄여」로 되돌림 입구 ingress-nginx 를 실증 창 동안 0 → 원복, CPU 요청 w1 705m·w2 505m 복귀 확인)**:
  - **dsync**(1노드 @w2): 진행 칸이 「파일 생성 · 대상 50,002 항목 → 데이터 복사 85% 현재 단계 기준 · 복사 약 1.0 GiB · 평균 34.5 MiB/s → 소유자·권한·
    시각 적용 → 도구 종료 · 전체 50,105/50,105」로 갱신되고, 수치(step·lines·walk)가 stdout.log 의 해당 줄과 정확히 일치. 폴링 5.01~5.13초, 숨김 탭 멈춤·
    복귀 즉시 1회, 응답 전부 no-store, 「남은」·left·line 키 0건, 잡 목록 `/progress` 0건. 배치 모드(검증용 batch_files 10000)에서 overall 19.958 → … → 100%
    가 「Copied N of 50105」 줄과 일치. 실행 중 stdout.log 가 자라고(스트리밍), launcher 로그는 DMS_JR_WORKER_READY 한 줄뿐(도구 출력 0).
  - **nsync**(2노드 w2 → w1, 검증용 batch_files 5000 → 11배치): 막대 9.1 → 18.2 → … → 100%, 복사 파일·데이터가 Progress 줄과 일치(copied_bytes 는 사람
    단위 근사 +0.022%). 기본 batch_files(배치 1개)는 「배치가 1개라 끝날 때 한 번 표시」 안내 뒤 끝에 100%.
  - **취소**: 복사 84% 에서 UI 취소 → Cancelled, stdout.log 에 복사 진행 줄까지 부분 출력이 남는다(예전엔 소실). progress.json 은 phase copy·`ended:true`
    (러너가 유예 안에 끝 스냅숏을 쓴다 — 설계 §8.4 C1 의 「ended 없음」 문구가 틀렸다), summary rc 205.
  - **효율(사용자 요구)**: progress.json 은 실측 129~400 B 고정 크기 덮어쓰기(이력 없음) — 22분 dsync 도 같은 크기. rename 쓰기라 공유 NFS 에서도 다른
    노드에서 보는 나이가 진행 줄 간격 수준(≤13초)이다.
  - **d49 실증 후속(d50)**: 종단 전이 뒤 아티팩트 목록 3·10·30초 재조회(취소 직후 칩이 새로고침 없이 +3초에 최종 — 실증), 느린 폴링(15초)은 available:false·
    오류 연속 3회부터(짧은 미리보기도 5초 뒤 끝 스냅숏을 받음 — 실증), nsync 배치 2개 이상이면 스캔·배치 단계 동안 캡션 「nsync 는 배치가 끝날 때마다
    갱신됩니다」(배치 단계 40틱 내내 — 실증), 배치 rm 동의 칸 포커스(위 위저드 항목).
  - **d50 실증 후속(d51)**: dsync 진행 칸이 walk 수를 사실 줄로 보인다(2초짜리 미리보기 끝 스냅숏에도 「소스 탐색 50,104 항목 · 목적지 탐색 0 항목」 —
    실증), 한 번 성공한 뒤 일시 available:false 에서 마지막 스냅숏 유지(d50 에서 공유 NFS rename 순간 1회 관측 — 라이브 재현은 안 돼 vitest 근거),
    미리보기 진행 중 「이 잡은 미리보기 요약이 저장되지 않았습니다」 숨김(2026-10-08 3f8f515c 부터 있던 결함 — 실증).
  - 남은 관찰(BACKLOG): 공유 base(NFS)에서 다른 노드가 보는 stdout.log 는 최대 약 30초 늦다(진행은 progress.json 몫), 취소된 dsync 목적지 루트 0700,
    바꿀 것 없는 재동기화의 결과 카드가 「모름」, 작은 파일 위주 복사에서 바이트 % 가 오래 멈춘 듯 보인다.

### ✨ 포탈 본문 열 최대 1440·가운데(A안) — 넓은 창에서 사이드바 오른쪽 영역 가운데, 화면 기준선은 하나 그대로 — **완료·실증**(2026-11-26, d48)

사용자(2026-11-26): 「A안으로 진행해 줘」 — 같은 날 넓은 모니터에서 본문이 왼쪽으로 쏠려 보이는 것을 고칠 초안 세 가지(A 전체 본문 열
1440 가운데 / B 화면 성격별 폭 4등급 / C 좁은 블록만 가운데)를 1920·2560 실 스크린숏과 h1 위치 실측으로 비교한 페이지(artifact
「포탈 가운데 정렬 초안」 https://claude.ai/artifact/NGhwDEuxDhSSK5dwCgUkxL)에서 A 를 골랐다. 기본값으로 정한 것: 최대 폭 1440(90rem,
`p-5` 패딩 포함 — 내용 1400), 넓은 표 화면 예외 없음, 화면별 폭(B)·좁은 블록 가운데(C)는 하지 않는다. 백엔드 무변경.

- **바꾼 것**: `frontend/src/app/AppShell.tsx` 의 `<main className="p-5">` → `mx-auto w-full max-w-[90rem] p-5` 한 곳(「왜」 주석). 사이드바
  (`aside` md:w-60·shrink-0 — e2e L3)·`div.flex-1.min-w-0`(L1)은 그대로, `main`·조상에 overflow 없음(SubmitLayout 요약 `xl:sticky`). 고정 폭이
  아니라 max-w 라 좁은 창에서 줄어든다.
- **1440 근거**: 사이드바 240 + 1440 = 1680 이하 창은 지금과 픽셀이 같다(여백 0 — 1280 실측 main.left 240·폭 1040 그대로). 1920 은 좌우 120,
  2560 은 좌우 440. 가장 넓은 실화면인 마운트 현황 표(E19 ⑩, 1920 에서 11열 ≈1272 — 래퍼 = 표 + 2 를 요구)가 내용 1400 안에 여유 있게
  든다(1360 이면 여유 48, 1280 이하는 E19 ⑩ 실패). 초안의 2560 스크린숏에서 사용량·전체 작업 표는 1400 으로 묶는 쪽이 경로 ↔ 숫자·상태
  칸 거리가 절반 이하로 줄어 오히려 읽기 쉬웠다 — 그래서 넓은 표 예외를 두지 않았다.
- **2026-08-16 「왼쪽 기준선」 결정과의 관계**(커밋 177f2105 — 빌드 화면만 mx-auto 라 글줄이 혼자 가운데로 튀었다 → 「제목·부제·탭·본문이 한
  기준선」): 원칙은 그대로이고 기준선이 놓이는 **열의 위치만** 가운데로 옮겼다. 화면(section)은 여전히 스스로 mx-auto 하지 않는다 — 모든
  화면의 크럼·h1·본문이 같은 x(1920 에서 380, 2560 에서 700)에 선다. B 는 화면마다 h1 x 가 튀고(1920 에서 260/380/504/620), C 는 한 화면
  안에서 제목과 본문이 갈려 고르지 않았다. 결정 기록 주석(BuildForm·BuildDetail·BuildHistory·SubmitLayout)의 문장을 「셸 본문 열(최대 1440,
  가운데)의 왼쪽 기준선 — 화면은 mx-auto 로 혼자 가운데로 가지 않는다」로 고쳤다(동작 불변). 규약 한 줄은 `frontend/README.md` 「규약」.
- **테스트**: vitest — `AppShell.test` 「본문 열」(main 클래스 mx-auto·w-full·max-w-[90rem]·p-5, 고정 폭 `w-[…]` 없음, main·조상 overflow 없음,
  부모 flex-1·min-w-0, aside md:w-60·shrink-0·mx-auto 없음), BuildForm·BuildHistory 의 「`.mx-auto` 없음」 단언은 뜻이 그대로라(화면만 렌더)
  이름·주석만 맞췄다. e2e — `helpers/layout.ts` 에 L5 `assertMainColumn`(main.width == min(1440, clientWidth − 240) ±0.5, 왼쪽 여백 ≥ 0,
  (main.left − 240) 과 (clientWidth − main.right) 차 ≤ 1 — 뷰포트가 아니라 `documentElement.clientWidth` 기준이라 고전 스크롤바 환경도 같은 식),
  E3 「본문 열」 — 1920×1080·1280×900 에서 대시보드·전체 작업·단일 작업·마운트 현황 네 화면마다 L1~L4 + L5, 1920 은 폭 ≤ 1440.5·왼쪽
  여백 > 10, 1280 은 main.left == 240 ±0.5·main.right == clientWidth ±0.5, 폭마다 네 화면 h1.left 동일(±0.5). E19 ⑩ 은 그대로 두어 「1400 열
  안에 11열 표가 든다」는 그물도 겸한다.
- **게이트**: vitest 2254 passed(130 파일), `tsc -b` 오류 0, `tsc -p tsconfig.e2e.json` 오류 0, `npm run build:image` 성공 · `dist/index.html`
  외부 URL 0건, e2e 43 passed(4.0m — 「본문 열」 1건 추가). 뮤테이션 세 가지로 이빨 확인: main 을 `p-5` 로 되돌리면 [L5] 폭 1680 > 1440,
  mx-auto 만 빼면 「가운데가 아니다(왼쪽 여백 0, 오른쪽 여백 240)」, 전체 작업 section 에 mx-auto max-w-5xl 를 넣으면 1920 「제목 왼쪽 568 ≠
  대시보드 380」.
- **라이브 실증(d48, 커밋 08cba773 · 오버레이 de2fb041)**: 실 Chrome 으로 대시보드·전체 작업·단일 작업·배치 생성·스토리지·마운트 현황·
  사용량·컨트롤 상태·빌드·노드 10개 화면 × 창 폭 1280·1680·1920·2560·390 을 쟀다. main 클래스는 모든 화면에서 `mx-auto w-full max-w-[90rem] p-5`.
  1280 은 main.left 240·폭 1040·여백 0/0, 1680 은 폭 1440·여백 0/0(예전과 같다), 1920 은 main.left 360·폭 1440·여백 120/120, 2560 은
  main.left 680·폭 1440·여백 440/440, 390 은 모바일 상단 바 아래 전폭(변화 없음). 폭마다 10개 화면의 h1 x 가 하나(260 / 260 / 380 / 700 / 20),
  가로 넘침 0, 콘솔·페이지 오류 0. 헤드리스 Chrome 은 오버레이 스크롤바라 clientWidth = 뷰포트 — 고전 스크롤바(≈15px) 환경이면 좌우
  여백이 7~8px 씩 줄어드는 것이 정상이다.
- **알아둘 것**: 효과는 창 폭이 1680 을 넘을 때만 보인다 — 1920 모니터에 OS 배율 125%(유효 1536)면 변화 0이다. 좁은 폼(단일 작업·
  배치 생성 1152, 설정 768, 빌드 672)은 열 안에서 왼쪽 정렬 그대로다(B 2단계는 사용자가 「폼도 가운데」를 원할 때만 — 08-16 「한
  기준선」을 뒤집는 결정이다).

### ✨ 마운트 현황 표 압축 레이아웃 — 열 폭 고정·「보고」 열 합침·열 머리 툴팁·100열 열 거르기·고정 칸 그늘·화면 높이 래퍼 — **완료·실증**(2026-11-25, d46 → d47)

사용자(2026-11-25): 「스토리지 - 마운트 현황에서 테이블 layout 이 깨져. 예쁘게 다시 고쳐줘. 테이블 칸 좌우가 너무 길어.. 그리고 스토리지가
100개가 될 수도 있어. 그때 마운트 현황은 좌우 스크롤이 필요할 것 같아.」 백엔드는 바꾸지 않았다(원인 9개 모두 프런트 CSS·구조).

- **진단(d45 라이브 + 100열 합성, 실 브라우저 실측)**: ① 표 `min-w-full` + auto 레이아웃이 남는 폭을 모든 열에 나눠 1920 에서 스토리지 열
  165~207px(칩은 56px — 칸마다 빈 폭 109~151px) ② 열 하한 `min-w-[8rem]`·`px-3` 로 실데이터 6열도 1280 에서 표 1142 > 래퍼 998(마지막 열
  잘림) ③ 이름 `whitespace-nowrap` 이 34자 이름 열을 289px 로 — 100열 표 19,493px ④ 머리 블록 넷이 1~3줄로 꺾이고 `align-bottom` 이라 열마다
  이름 높이가 다름(머리 113~197px) ⑤ 11px 글자가 text-sm 줄높이 20px 를 물려받음 ⑥ 노드 꼬리표 `max-w-[14rem]` 줄바꿈이 행 높이를 정함
  ⑦ 「리포트 지연 · 12분 전」 칩이 보고 열을 137px 로 ⑧ 고정 열 경계가 1px 선뿐(모서리엔 없음) ⑨ 100열에서 열 추적 수단 없음(세로선·열
  강조 없음). 가로 스크롤바(래퍼 바닥)는 23노드 1280×900 에서 1020px — 첫 화면 밖. hover 마다 표 전체 재렌더(CPU 4x 에서 약 85ms).
- **바꾼 것**(`frontend/src/features/mounts/` — 공용 코어 MountMatrix 재작성·MountCell·mountTerms·MountStatusPage·NodeMountCards·NodeMountSheet(슬롯 하나), dms-k8s
  덧붙임 k8sMountParts, `tailwind.config.ts` 의 `shadow-pin-x|y|xy`):
  - 열 폭 고정 — 스토리지 6.5rem(칩 상한 6rem + px-1; 「마운트 빠짐」 칩 ≈ 88.5px), DMS 공용 8rem, 노드 열 안쪽 11rem. auto 표에서 열을 묶는
    방법은 머리 내용 절대폭 상자(`w-24`) + 칸 칩 절대 상한(`max-w-24`)이다(퍼센트 max-w·truncate 는 열을 못 묶는다, table-layout fixed 는
    width:max-content 와 함께 브라우저마다 달라 안 쓴다). 표 `w-max` + 래퍼 `w-fit max-w-full` — 1280 실데이터 6열 ≈ 952px(넘침 0), 100열
    ≈ 10,820px(-45%), 1920 에서는 카드가 표 폭에 맞춰 좁아진다.
  - 「보고」 열 삭제 → 고정 노드 열 이름 줄 오른쪽의 `<time>`(지연 = 붉은 글자 + sr-only 「리포트 지연 · 」, KST 는 title; 행마다 자기 15초
    틱). 문제 꼬리표는 꼬리표 줄 맨 앞(`NodeTags` lead — 카드는 그대로 이름 옆).
  - 열 머리 = 위쪽 정렬의 이름(두 줄 고정 칸 — `columnNameLines`: 13자 이하 한 줄, 26자 이하는 두 조각 다 13자 이하인 마지막 하이픈
    뒤에서 **직접** 두 줄, 넘으면 앞 조각 말줄임 + 「…」끝 12자)·경로(14자 넘으면 「…」 + 끝 조각 `columnPathTail` — 끝 13자 안의 「/」
    경계 끝, 짧으면 끝 13자)·k8s 종류 꼬리표 하나(StorageClass 매핑 없음이면 주의색 + ⚠, PV 수 모름이면 물음표 표지, 꼬리표 안 sr-only
    로 「선택 가능한 PV n개·모름 · 클러스터 n」)·요약(「마운트 r/t」 + 문제·읽기 전용은 아이콘 + 수). 보이는 조각은 aria-hidden, 낭독은
    sr-only 전체 한 번. 전체 이름·경로·요약 문장(`columnSummaryText`)·기준·선택 가능한 PV·클러스터·StorageClass 는 **열 머리 툴팁**(새
    슬롯 `renderStorageTip` = `K8sStorageTip` — 같은 슬롯을 노드 시트의 스토리지 줄에도 넘긴다: 툴팁은 마우스 전용) — th `title` 은
    없앴다(내장 툴팁이 겹친다). 칸 칩 `px-2 → px-1.5` + 글자 말줄임(모르는 원문 사유 코드도 칩 상한 안), 11px 글자 `leading-4`.
  - 100열 보조 — `?q=` 스토리지 이름·경로 찾기(전체 작업 `useSearchField` — 300ms·한글 조합, 딥링크는 첫 렌더부터), `?cols=problems` 문제
    있는 스토리지만(`columnProblem` = 서버 `_ro_outlier_columns` 의 열 쪽 거울: 마운트 문제, 또는 신선한 ready 가 있는 열의 읽기 전용).
    표·카드에 걸고 노드 시트는 모든 스토리지, 0열이면 「조건 지우기」, 거르는 동안 안내 줄(타일·범례·노드 범위·「문제 n」은 전체 기준)과
    카드 요약 줄 「(표시한 스토리지 n개 기준)」. 열 hover 면 그 열 머리 `data-hot` = 머리 바닥 accent 막대(배경 그림 — 면색은 그대로),
    스토리지 칸 옅은 세로선.
  - 고정 칸 그늘 — 래퍼 `data-scroll`(x|y|xy) + `group-data-[scroll=…]/mx:shadow-pin-*`(민 방향으로만, 모서리 포함, React 상태 없음).
    래퍼 높이 상한 = 화면에 남은 높이(`useFitMaxHeight` — 창 높이 − 래퍼 문서 위치 − 16, 최소 22rem; resize·ResizeObserver(body)) — 가로
    스크롤바가 첫 화면 안. 래퍼 role=region·tabIndex 0(화살표 키로 민다).
  - 성능 — 칸·열 머리 툴팁을 래퍼 한 곳의 mouseover/mouseout 위임 + `MatrixTip` 층(useImperativeHandle)으로, `MatrixHead`·`MatrixRow` memo,
    열 목록 원소 동일성 안정화(`useStableList`), 노드별 파드 memo, k8s 슬롯은 모듈 상수 — hover·스크롤·15초 틱에 표 전체를 다시 그리지 않는다.
- **결함(구현 중)**: 「조건 지우기」가 `cols=problems` 를 되살렸다 — 라우터의 주소 갱신이 전환이라 검색 적용값보다 한 박자 늦게 오는데, 적용값 →
  주소 효과가 그 사이의 옛 주소(`setSearchParams` 함수형의 인자도 렌더 시점 값)로 q 만 지워 덮어썼다. 「마지막으로 보거나 쓴 주소 값」(lastUrlQ)과
  비교하도록 고쳤다(vitest 「열 거르기」가 잡았다).
- **리뷰 반영(같은 날)**: ① 경로 줄이 앞 조각 말줄임(약 13자)이라 프로덕션 `/mgmt_storage/…` 열이 모두 「/mgmt_storage…」로 똑같이
  보였다 → 끝 조각(`columnPathTail`). ② 24자 이하 이름도 브라우저 자연 줄바꿈(하이픈 뒤에서 먼저 꺾고 「-01」 앞은 못 꺾는다)이면 세 줄이라
  두 줄 칸에서 끝이 잘렸다(「weka-research-archive-01」 → 「weka-」/「research-…」) → 줄을 직접 나눈다(`columnNameLines`, 문턱 24 → 26).
  ③ PV 수·「모름」(null ≠ 0)·클러스터가 hover 툴팁에만 있어 키보드·스크린리더·터치로 얻을 길이 없었다 → 종류 꼬리표 안 sr-only 낭독 + 모름
  물음표 표지 + 노드 시트 스토리지 줄에 `renderStorageTip`. ④ 열 따라가기 면색(infobg)이 PV 꼬리표(Tag info) 면과 같아 hover 중 꼬리표가
  맨 글자처럼 보였다 → 바닥 막대(box-shadow 는 고정 칸 그늘이 써서 배경 그림). 주의: 주석에 옛 클래스 이름을 그대로 적으면 Tailwind JIT 가
  그 규칙을 다시 만든다 — 주석은 클래스 글자를 피했다(e2e 가 머리 면색 ≠ 꼬리표 면색을 잰다). ⑤ `?q=` 로 거르면 「문제 3」 꼬리표(전체 기준)와
  카드 요약 줄 「마운트 문제 1」(거른 열 기준)이 말없이 어긋났다 → 안내 줄 + 「(표시한 스토리지 n개 기준)」. ⑥ 공용 코어 MountStatusPage 가
  dms-ssc 에 없는 `../jobs/searchField` 를 새로 import → 머리 주석·ARCHITECTURE 에 「이식 때 함께」 + `mountTerms.test` 이식 의존 그물(코어의
  바깥 import = dms-ssc 에도 있는 목록 ∪ 이식 때 함께 가져갈 목록, 뮤테이션으로 빨간불 확인). ⑦ 열 머리 상세의 키보드 경로 = ③의 노드 시트.
  ⑧ `ProblemTag` 독스트링(표는 꼬리표 줄 맨 앞, 카드는 이름 옆).
- **테스트**: vitest — mountTerms(columnProblem·filterColumns·columnSummaryText·columnNameLines·columnPathTail·이식 의존 그물),
  MountCell(StorageTipBody·NodeTags lead), k8sMountParts(꼬리표 하나 + sr-only PV 수·모름 표지·매핑 없음·K8sStorageTip·노드 시트 덧붙임),
  MountStatusPage(행 머리 `<time>`·머리 압축·머리 툴팁·이름 두 줄·경로 끝 조각·열 거르기 URL·거른 범위 안내·카드 딥링크·래퍼 data-scroll·
  100열 스모크 — 픽스처 `wide(n, nodes)`). e2e — E19 에 100열 합성 시나리오(`e2e/helpers/mountWide.ts`, 1280·1920 — 26자 이하 이름 두 줄 안·
  하이픈 25자 이름·같은 `/mgmt_storage` 접두 경로가 열마다 다른 끝 조각·경로 넘침 0·열 따라가기 막대가 꼬리표 면을 지우지 않음 포함), E3 마운트
  셀 하한 8 → 6·보고 시각 셀렉터 `th time`.
- **게이트(리뷰 반영 뒤)**: vitest 2253 passed(130 파일), `tsc -b` 오류 0, `tsc -p tsconfig.e2e.json` 오류 0, `npm run build:image` 성공 ·
  `dist/index.html` 외부 URL 0건, e2e 42 passed(3.9분). 백엔드 무변경.
- **남은 것**: 실 라이브 확인(d46+ 빌드·릴리스 뒤 1280·1920 실데이터 6열이 래퍼 안에 다 들어가는지, 테스트베드 글꼴에서 이름·경로 줄이 넘치지
  않는지). 열 머리의 요약 문장·기준 문장은 마우스 툴팁 전용이다(같은 수는 머리 칩이 sr-only 낱말과 함께 말하고, PV·클러스터·StorageClass 는
  노드 시트에 있다). 등폭 글꼴(`font-mono` — 번들이 아니라 시스템 글꼴)의 advance 가 0.6em 보다 넓은 사이트면 13자/14자 줄이 1px 안팎 넘쳐
  끝 글자가 말줄임될 수 있다. 수십 대 × 100열(5천 칸 이상) 규모의 열 가상화·열 정렬·「보고」·「DMS 공용」 열 고정은 이번 범위 밖(열 순서는
  응답 순서 고정). 좁은 화면 카드에서 모르는 원문 사유 칩이 스토리지 이름을 한 글자까지 밀어내는 것은 그대로다(카드 레이아웃은 손대지 않았다).

**배포·라이브 실증(d46)**: 포탈 빌드 d46(소스 7a172d1d) → 릴리스 4종 Applied → 오버레이 d45 → d46(a06b111b). 실 Chrome(헤드리스 — 스크롤바가 그려지지
않아 래퍼 바닥 위치로 판정), 100열은 진단 때의 page.route 재현(route.fetch 없음):
- 실데이터(노드 2 × 스토리지 6): 1280 표 952px = 래퍼(전 1142 > 998 — 마지막 열 잘림), 1920 스토리지 열 104/105px(전 165~207), 칸당 빈 폭 평균 1920
  129 → 53px(−59%)·1280 78 → 53px, 칩 중심 간격 1280 128~139·1920 178~202 → 105px. 머리 111px 로 모든 열 같음(전 113~197, 열마다 이름 위치
  어긋남), 행 92/65 → 81/56px.
- 100열: 표 폭 19,493 → 10,822px(−44.5%), 첫 화면에 보이는 스토리지 열 1280 약 2.5 → 6.4·1920 약 6 → 12.5, 페이지 가로 넘침 0(처음·반쯤 민·가로세로
  민 화면 모두), 23행 래퍼 바닥 1280×900 1020 → 884px(가로 스크롤바가 첫 화면 안), 고정 노드 열·모서리·머리 그늘, 열 hover 머리 막대, 이름 두 줄·
  긴 이름 끝 조각·경로 끝 조각, 열 거르기 ?q=nfs 31/100·?cols=problems·조합·0개면 「조건 지우기」, 래퍼 Tab 초점 → 화살표 가로 스크롤. 390 카드
  보기 회귀 없음. 콘솔·실패 요청 0.
- 실증 뒤 다듬기(d47): 「DMS 공용」 열 머리가 스토리지 열 머리보다 16px 위였다 → 같은 두 줄 고정 높이 블록. 래퍼에 초점이 있을 때 Home/End = 가로
  처음·끝(화살표 40px 씩이라 100열 끝까지 약 246번이었고 Home/End 는 페이지를 세로로 움직였다 — e2e E19 단언 추가).
- 남은 것(관찰): 끝 13자에 「/」가 없는 경로는 단어 중간부터(「…hfs-secondary」), 끝부분이 같은 경로는 머리만으로 구별이 안 됨(툴팁·시트에 전체),
  요약 칩 셋인 열이 있으면 머리 전체가 133px, 첫 행 칸 툴팁이 그 열 머리 막대를 덮음.

### ✨ stale 노드 기록 정리(기록 삭제·자동 정리·새 이름 힌트) + 노드 상세 「관리 디렉토리」 열 + 빌드·릴리스·컨트롤 상태·아티팩트 경로를 관리 메뉴로 — **완료·실증**(2026-11-25, d44 → d45)

사용자(2026-11-25): ① 「스토리지 - 노드에서 상세 버튼을 클릭하면 뜨는 마운트 현황 테이블에 … 마운트 경로뿐 아니라 관리 디렉토리 열도 테이블에
추가해 줘」 ② 「빌드, 릴리즈, 컨트롤 상태, 아티팩트 경로를 포탈에서는 운영 메뉴 밑에 있는데 관리 메뉴 밑으로 넣어줘」 ③ 프로덕션(OA 사이트) 배포 중
이슈 — 포털 노드 목록의 「사라지지 않는 stale 노드」 정리 경로(기능 추가, 기존 동작 불변).

- **증상(OA 실측 2026-11-25)**: 호스트 이름 h-aii-dms26001 → h-aii-dms-26001. RKE2 가 노드를 새 이름으로 다시 등록했고 에이전트(노드 이름 =
  `spec.nodeName` — `50-agent-daemonset.yaml` `DMS_AGENT_NODE_NAME`)는 새 이름으로 정상 보고한다. 옛 이름은 마지막 보고(2026-11-19) 그대로 「리포트
  지연」으로 영원히 남아 같은 장비가 두 줄 — 노드 수·마운트 현황·지표가 지저분하고 지울 방법이 없었다.
- **원인**: `repositories/agents.py` 는 보고 때만 agent_nodes 를 갈아 끼우고(INSERT agent_reports + DELETE/INSERT agent_nodes) 지우는 경로가 없다.
  `retention.prune_agent_reports_once` 는 이력(agent_reports)만 지운다. `/api/admin/nodes` 엔 배치 제외 DELETE 만 있었다. 배치 판정은 fresh 보고만
  쓰므로(`fresh_reports`) 옛 행이 작업을 받지는 않는다 — 문제는 표시·집계 잡음과 운영자가 정리할 수 없다는 것.

**① 노드 기록 삭제 `DELETE /api/admin/nodes/{name}`**(`api/routes_nodes.py` → `node_forget.forget_node` → `AgentsRepository.forget_node`)
- **세션 관리자만**(`require_session_admin` — 공유 토큰 403 `admin_session_required`, 비관리자 403 `admin_required`, 무인증 401). 공유 토큰은 모든 노드
  에이전트가 가진 role admin 자격이라 노드 기록과 배치 제외·보고 이력(마지막 cordon 의 근거)을 지울 수 있으면 안 된다. 유지보수 중에도 허용(배치
  제외와 같은 정리 동작).
- **한 트랜잭션**: agent_nodes 행 + node_exclusions 행(있으면) + 그 노드의 agent_reports 전부 + 감사 1행(`mutation_class='agent_node'`, `operation='delete'`,
  actor = 세션 사용자, before = 마지막 보고 시각·배치 제외 행·지운 이력 수 — 보고 본문은 크므로 싣지 않는다). 200 = `{deleted, reported_at,
  reports_deleted, exclusion_deleted}`. 같은 이름이 다시 보고하면 새 행으로 다시 생긴다(의도).
- **거절**: 404 `node_not_found`, 409 `node_fresh`(GET 의 fresh 와 같은 판정 — 살아 있는 노드는 지워도 다음 보고에 다시 생긴다), 409
  `node_in_use:<토큰,…>`(문자열 합성 — 포탈 `request()` 는 문자열 detail 만 읽는다; 사유 코드 그물은 f-string 접두를 뽑는다). 토큰과 출처(코드로 확인 —
  `node_forget.REF_KINDS` 순서): `build_node`(control_state.build_node_name), `k8s_primary`·`k8s_secondary`(k8s_sync_settings.primary_node·secondary_node),
  `active_build`(새 `BuildsRepository.active_on_node` — 그 노드의 Pending·Running 빌드; 빌드 노드를 바꾼 뒤에도 옛 노드에서 돌 수 있다),
  `active_jobs`(새 `DataJobsRepository.active_jobs_on_node` — 비종단 잡의 worker_pool.candidates). plans 표에는 노드 열이 없고, 표시용 포인터
  (control_state.artifact_base_check_node·api_base_checks.node_name)는 근거가 아니다. `active_jobs` 면 막는 요청 id 를 최대 5개 상세 꼬리
  `;requests=<id,…>` 로 싣는다(판정과 같은 스냅숏 — 작업 목록엔 노드 필터가 없어 운영자가 찾을 길이 없었다, 리뷰 반영). 포탈 창은 요청 상세 링크로 보인다.
- **비종단 잡을 막는 이유**: stepper·`node_exclusions.blocked_nodes` 가 `latest_reports`(신선도 무관)로 후보 노드의 마지막 cordon 을 본다 — 행을
  지우면 fail-open 이고, ConfirmPending 잡이 확인 뒤 사라진 노드로 제출되면 gang 이 영원히 Pending 이다. 그래서 정확해야 한다: LIKE 사전 필터(JSON
  직렬화한 이름 + 와일드카드 이스케이프 + `ESCAPE '\'`) 뒤 파싱해 candidates 에 이름이 있는 행만, **깨진 JSON·dict 아님에 이름이 걸리면 쓰는 것으로
  본다**(fail-closed). planner 순서용 `active_worker_pools`(상한 2000·깨진 행 건너뜀)는 쓰지 않는다.
- **조건부 DELETE**: 존재·stale·in_use(같은 커넥션의 읽기) 판정 뒤 `DELETE … WHERE reported_at <= 임계` — 0행이면 다시 읽어 404/409. SELECT … FOR UPDATE
  를 쓰지 않은 이유: PG READ COMMITTED 에서 잠근 옛 행이 동시 ingest(DELETE+INSERT)로 사라지면 그 행을 건너뛰어 「없음」으로 잘못 답한다. 일회용 PG 16
  두 커넥션으로 「판정 뒤·삭제 전에 다른 커넥션의 보고가 커밋」을 재현 — `node_fresh` 로 거절, 새 행·이력 2건 그대로.
- **받아들인 위험**(docstring): (a) 이력이 노드당 최대 약 4.3만 행(보존 30일 × 하루 1440 보고)이라 한 트랜잭션이 수 초 — API 단일 커넥션이 그동안
  막힌다(라이브 실증에서 잰다), (b) retention 청크 삭제와의 드문 PG 교착(500 — 다시 누르면 된다), (c) in_use 판정 뒤의 좁은 계획 창(planner 는 fresh
  노드만 쓴다).

**② 포탈 노드 화면**(`features/nodes/NodesList.tsx`·새 `ForgetNodeDialog.tsx`·`useNodes.useForgetNode`)
- 「리포트 지연」 행의 상태 칸에만 「기록 삭제」 버튼(9열 유지) + **페이지 수준** 확인 창 하나(연 순간 스냅숏 — 행 안에 두면 성공·409 뒤 재조회로 함께
  언마운트돼 콜백·포커스·오류 문구를 잃는다; 보안 이벤트 `GlobalUnlockButton` 과 같은 이유). 창: 되돌릴 수 없음, 같은 이름이 다시 보고하면 새로
  생김, 배치 제외도 함께 지워짐(있으면), 막는 것(빌드 노드·k8s 호출 노드·진행 중 작업·빌드), 마지막 보고. `node_in_use:` 는 근거 토큰을 한국어로
  옮겨 보이고, 막는 요청(`;requests=`)은 요청 상세(`/jobs/<id>`) 링크로 그린다. 결과 줄은 늘 그려 둔 role=status(성공 뒤 포커스). 사유 코드 `node_fresh`·`node_in_use` 를 `reasonCodes.json`·`REASON_MESSAGES` 양쪽에,
  `admin_session_required` 문구에 「노드 기록 삭제」.

**③ 자동 정리(선택, 기본 꺼짐)** — `node_forget.forget_absent_nodes_once`, 컨트롤러 retention 루프(`controller._retention_step`)의 **맨 끝**(새 루프 없음 —
루프 목록 고정 테스트 불변, k8s 실패가 위 prune 들을 막지 않는다).
- `DMS_AGENT_NODE_FORGET_SECONDS`(기본 0 = 끔, 켜면 예 604800): 그 시간 이상 보고가 없고 **k8s Node 목록에 그 이름이 없고** in_use 가 아닌 노드만, 같은
  몸통으로 틱당 **지운 수** 20개까지(in_use 거절은 세지 않는다 — 시도 수에 걸면 이름순 앞자리의 in_use 노드가 매 틱 예산을 다 써서 뒤의 노드가 영원히
  남는다, 리뷰 반영; 감사 `auto_forget`·actor `system:agent-node-forget`, 이벤트 `agent_node_forgotten`). 꺼짐·후보 0 이면 k8s 를 부르지 않는다. 음수와
  `0 < 값 < DMS_AGENT_REPORT_STALE_SECONDS` 는 기동 거부. base `20-config.yaml` 에 `"0"`(코드 기본과 같은 사이트 중립값 — 손잡이 한곳).
- **모름은 지우지 않는다**(null ≠ false): 조회 예외 → 이벤트 `agent_node_forget_skipped`(`k8s_list_failed`), 빈 목록·문자열 이름 없음 → 같은 이벤트
  (`no_nodes_listed` — 스텁 백엔드의 `build_node_lister` 는 `lambda: []` 라 「노드 없음」으로 읽으면 로컬·CI 의 오래된 행이 전부 지워진다), in_use →
  `agent_node_forget_in_use`(payload 에 막는 요청 id). k8s 조회는 api 의 no_proxy 힌트와 같은 `wiring.build_node_lister`(core `list_node`, 10초) — 새 k8s 코드 없음, cli 의 `--once`·
  상주 두 경로에 배선(`test_node_forget` 의 cli 배선 가드).
- **RBAC — 매니페스트 변경**: 컨트롤러 SA 에는 nodes 권한이 없었다(namespaced Role 뿐 — 그대로면 403 → 아무것도 안 지움). 새 ClusterRole 을 만들지 않고
  `10-rbac.yaml` 에 ClusterRoleBinding **`dms-controller-nodes-readonly`**(roleRef ClusterRole `dms-api-nodes-readonly` — nodes get·list, subject SA
  `dms-controller`)를 더했다. 계약 `tests/test_rbac_contract.py`(바인딩 존재·ClusterRole 읽기 전용 그대로·컨트롤러 Role 에 nodes 없음).
- **한계 — 호스트 이름만 바꾼 경우**: 클러스터가 새 이름으로 Node 를 다시 등록해도 옛 Node 객체는 보통 NotReady 로 남는다(베어메탈·RKE2 는 아무도
  지우지 않는다) — 그동안 자동 정리는 그 행을 지우지 않는다. OA 사례는 버튼(①②)으로 정리하거나 클러스터 운영자가 옛 Node 를 지운 뒤에 돈다(deploy/README
  §14-3 ⑤, 리뷰 반영).

**④ 같은 장비의 새 이름 힌트** — 에이전트 보고에 `machine_key`(`agent/probes.probe_machine_key`): 호스트 루트 모드에서만 `/host/root/etc/machine-id` 를
O_NOFOLLOW·정규 파일·32 hex 검증 후 HMAC-SHA256(key = machine-id 16바이트, msg = `dms-agent/machine-key/v1`) 앞 16 hex — **원문은 어디에도 싣지 않는다**.
direct 모드·없음·「uninitialized」·전부 0 은 null, 프로브 예외도 null(보고는 그대로). 서버 `node_forget.rename_hints` 가 stale 행의 키와 같은 키를 가진
**이름이 다른 fresh 행이 정확히 1개**일 때만 GET 행의 `renamed_to` 로 낸다(복제 VM 템플릿처럼 2개 이상이면 없음). 포탈은 「새 이름: X」. 표시뿐 —
노드 이름 = k8s Node 이름이 계약이라 병합·개명은 하지 않는다. 스키마 변경 없음(보고 JSON 안의 키 하나). **한계**: dms-agent 롤아웃 뒤의 보고부터 —
OA 의 h-aii-dms26001 처럼 이미 멈춘 옛 이름에는 힌트가 없다(버튼으로 정리).

**⑤ `list_nodes` 호출자 감사**(grep 전수 — 과제 목록 밖 `routes_usage` 1곳 추가 발견) — **계산에 stale 행이 섞이는 곳은 없다, 고친 곳 0**:

| 호출자 | 처리 | 판정 |
|---|---|---|
| `routes_nodes.list_nodes` | 표시(fresh 플래그·배치 제외) — `renamed_to` 만 더함 | 표시 |
| `routes_metrics.metrics_nodes` | 노드별 시계열 카드 + fresh, 노드 간 합산 없음 | 표시 |
| `routes_k8s._nodes` | 호출 노드 카드의 fresh·reported(보고 지연 표기) | 표시 |
| `routes_artifact_base._node_checks` | stale → pending(exists·writable null) — 화면 「확인 대기 N건」. 에이전트만 죽은 살아 있는 노드도 stale 이라 빼면 「모두 정상」이 거짓말 | 의도된 모름(퇴역 행은 기록 삭제로) |
| `request_purger._eligible_nodes` | `if not node["fresh"]: continue` | 계산 — 이미 fresh 만 |
| `routes_builds.submit_build` | fresh 집합으로 `build_node_report_stale` | 계산 — 이미 fresh 만 |
| `routes_mount_status` → `mount_status.build` | 열 요약·칸 합계·과반·ro outlier 는 `fresh_rows`; totals.nodes·stale_nodes·problem_nodes 는 stale 포함(신선하지 않은 행은 문제 — docstring 의 의도) | 계산은 fresh 만 |
| `routes_usage` → `metrics_series.storage_filesystem` | fresh 우선, 같은 신선도 묶음만으로 node_count·consistent, fresh 후보가 없을 때만 stale 폴백 + `fresh:false` 표시 | 섞이지 않음 |
| (참고) `fresh_reports`(reconciler·planner) / `latest_reports`(stepper·`blocked_nodes`·claim) | fresh 만 / **의도적으로 신선도 무관**(마지막 cordon 을 잊지 않음) | 그래서 `active_jobs` 가드 |

**⑥ 노드 상세 「마운트」 표의 「관리 디렉토리」 열** — 사용자가 말한 표는 마운트 현황 페이지가 아니라 노드 상세 창의 「마운트」 절이다. 에이전트
마운트 보고엔 managed_root 가 없어 **프런트 조인**(`useStorages` — 지금 등록된 스토리지 값, 상세를 열 때만 읽는다; 모름·미등록은 「—」). 열 순서
스토리지 | 마운트 경로 | 관리 디렉토리 | 상태 | 쓰기 | 사유. 마운트 현황(`mount_status.build`, dms-ssc 공용 코어)은 바꾸지 않았다(BACKLOG 후보).

**⑦ 사이드바** — 빌드·릴리스·컨트롤 상태·아티팩트 경로를 「운영」에서 「관리」 그룹 끝(포탈 설정 뒤, 같은 상대 순서)으로(`app/navigation.ts` — 경로·화면·
상세 경로의 부모는 그대로, 크럼만 「관리」). 운영 그룹엔 대시보드·사용량 분석이 남는다. deploy/README 의 「운영 → 빌드·릴리스·컨트롤 상태·아티팩트 경로」
표기 8곳을 「관리 → …」로.

- **배포**: 이미지 릴리스만으로 삭제 API·버튼·메뉴·열이 동작한다(기본 0 이면 S11 불필요). 힌트는 dms-agent 롤아웃 뒤 새 보고부터. 자동 정리를 **켤
  때만** S11(prod `sh deploy/install.sh --context <ctx> --site <site> --only S11` — 바인딩은 cluster-admin, 사이트 patch-config 의 키 → dms-config → 재시작).
  testbed-k8s 는 `kubectl --context dmsk8s apply -k deploy/overlays/testbed-k8s` 뒤 ConfigMap 만 바뀌었으면 rollout restart. OA 정리 절차: 이미지 릴리스 →
  노드 화면에서 h-aii-dms26001 「기록 삭제」(그 이름이 빌드 노드·호출 노드·진행 중 작업에 걸려 있으면 먼저 정리).
- **테스트**: 새 `tests/test_node_forget.py`(삭제 API 성공·fresh·404·인증 3갈래·유지보수 중 허용·in_use 다섯 갈래와 순서·종단 빌드/잡은 막지 않음·
  candidates 만·깨진 JSON fail-closed·LIKE 이스케이프·막는 요청 id(중복 제거·5개 상한·모양 불량 제외)·조건부 DELETE 경합 둘·힌트·자동 정리 켜짐/꺼짐/
  Node 있음/없음/조회 실패/빈 목록/lister 없음/창 미만/in_use(이벤트의 막는 요청)/틱 상한(지운 수 — 앞자리 in_use 21개 뒤의 노드도 지운다)/retention
  루프 배선/cli 배선·설정 범위·20-config), `test_rbac_contract`(바인딩 3), `test_agent_probes`(machine_key 파생·direct·형식 불량·끝 링크·정규 파일
  아님), `test_agent_runner`(키 싣기·프로브 예외), `test_api_nodes`(`renamed_to`). sqlite 와 일회용 PostgreSQL 16 양쪽 통과. 프런트: NodesList(열·버튼
  노출 조건·창·DELETE·409/403 문구·막는 요청 링크·힌트), navigation(관리 11·운영 2·크럼).
- **게이트(2026-11-25, 리뷰 반영 뒤 최종)**: 백엔드 전체 6321 passed · 19 skipped(9분 42초), 프런트 vitest 2235 passed(130 파일), `tsc -b` 오류 0,
  `npm run build:image` 성공·`dist/index.html` 외부 URL 0, e2e 41 passed(19 spec — 사이드바 그룹 소속·노드 표 9열 단언 그대로).
- **문서**: deploy/README §14-3 「노드 이름이 바뀐 뒤(또는 클러스터에서 뺀 뒤) 옛 행 정리」(절 번호 이동 없음)·§0-4 클러스터 범위 객체 표·§14-1
  운영 신호·보존 줄·§14-4 색인, ARCHITECTURE(모듈 지도·노드 배치 제외 불변식·공유 토큰 경계), BACKLOG(「agent_nodes 퇴역 행 정리」 해소 + 「노드 기록 정리
  후속」).
- **dms-ssc 이식 후보**: 삭제 API·버튼·힌트는 라인 공통 성격(옛 이름 문제는 LDAP 과 무관). 자동 정리는 ssc RBAC 에도 같은 바인딩.

**배포·라이브 실증(d44)**: 포탈 빌드 d44(소스 20950c48, 3종) → 릴리스 4종 Applied → 오버레이 d42 → d44(fe42d615) → 설치기 `--only S11` PASS
(ClusterRoleBinding dms-controller-nodes-readonly 생성 — `auth can-i list nodes --as=…:dms-controller` yes·delete no, `DMS_AGENT_NODE_FORGET_SECONDS`
"0"). 실 Chrome:
- 메뉴: 운영 = 대시보드·사용량 분석, 관리 끝에 빌드·릴리스·컨트롤 상태·아티팩트 경로(크럼 「관리 > …」, 사이드바 aria-current 1개).
- 노드 상세 마운트 표: 스토리지·마운트 경로·관리 디렉토리·상태·쓰기·사유, 6개 스토리지 관리 디렉토리가 GET /api/admin/storages 의 managed_root 와 같음.
- machine_key: k8s-w1·w2 가 서로 다른 16hex, renamed_to null, 응답 어디에도 machine-id 원문 없음.
- 가짜 노드 `proof-oldname`(mounts 빈 목록, machine_key = k8s-w2 와 같게) 보고 → 프라이머리에서 reported_at 을 1시간 전으로 → 「리포트 지연」·「새 이름:
  k8s-w2」·「기록 삭제」(정상 행에는 버튼 없음). 빌드 노드를 잠시 그 노드로 → 확인 → 409 `node_in_use:build_node`·창 「(막는 것: 빌드 노드)」 → 4초 만에
  원복(컨트롤 상태 21키 대조 — 다른 것은 changed_at·artifact_base_check_at 뿐). 공유 토큰 403 `admin_session_required`, k8s-w2 409 `node_fresh`(이력
  그대로), 없는 이름 404. 버튼 삭제 성공(200, 39ms) → 결과 줄로 포커스·목록 2대, agent_nodes·agent_reports·node_exclusions 0건, audit_log
  `agent_node/delete/proof-oldname/mason` 1행. 회귀 화면 콘솔·실패 요청 0.
- 실증에서 찾은 것(d45 에서 고침): 노드 상세 마운트 표가 390 폭에서 「마운트 경로」·「관리 디렉토리」를 15px 로 줄여 경로가 글자마다 세로로 꺾였다(경로 칸
  `break-all` 이 열을 한 글자 폭까지 허용 — 마운트 경로 열은 원래 그랬고 새 열이 물려받음), 1280 에서도 긴 경로면 쓰기 열이 「아니 오」로 꺾임 → 경로 두
  열 최소 9rem + 필요할 때만 줄바꿈(좁으면 표 안 가로 스크롤), 상태·쓰기 nowrap, 사유 필요할 때만 줄바꿈. stale 칸과 옆 「배치 제외」 버튼 간격(pr-3).

### 🔧 빌드 파드 메모리 상한 1Gi → 2Gi + 프런트 빌드 Node 힙 1024MB 고정 — **완료·실증**(2026-11-23, d42·d43)

사용자(2026-11-23): 「이미지 빌드 메모리 부족이 왜 발생한거야? 빌드 파드 메모리 상한을 늘려봐. 이거는 테스트베드에 한해서 발생하는 이슈야?」

- **왜**: 포탈 빌드 파드의 memory limit 은 코드 상수 1Gi(`build_manifests` — 옛 테스트베드 워커 allocatable 1355Mi 역산, 「노드 축출 전에 빌드가 먼저
  혼자 죽는」 1차 방어)였다. Node 는 V8 힙 상한을 컨테이너(cgroup) 상한의 약 절반으로 잡는다(실측 약 505MB). 포탈 타입 검사(`tsc -b` — 테스트
  128개 파일 포함)가 힙 510~550MB 로 커지자 d37 빌드가 `heap out of memory`(exit 134)로 죽었다(바로 아래 항목 — 그때는 이미지 빌드를 앱 코드만
  검사(`build:image`, 힙 약 400MB)로 바꿔 넘겼다).
- **테스트베드만의 문제가 아니다**: 빌드 파드 자원은 사이트 설정이 아니라 코드 상수라 모든 사이트가 같고, 힙 상한은 노드 메모리가 아니라 파드 상한이
  정한다 — 프로덕션도 같은 소스를 빌드하면 똑같이 죽는다.
- **바꾼 것**: 빌드 컨테이너 memory limit 1Gi → **2Gi**(requests 128Mi 는 그대로 — 압박 때 kubelet 축출·커널 OOM 순위(oom_score_adj 약 958)에서
  여전히 빌드가 맨 앞), Dockerfile.dms web 단계에 `ENV NODE_OPTIONS=--max-old-space-size=1024`(자동 계산 — Node 판마다 다를 수 있다 — 대신 고정, 런타임
  이미지로는 넘어가지 않는다). `build:image`(앱 코드만 검사)는 유지 — 메모리·시간이 덜 든다.
- **실측**(로컬, 힙 1024MB 고정): `build:image` 통과·최대 RSS 628MB, 테스트 포함 전체 타입 검사도 통과·812MB — 2Gi 안에서 여유 충분(힙 여유 약 2.5배).
  테스트베드 빌드 노드 k8s-w1 은 총 2.9GiB·가용 약 1.4GiB(에이전트 보고) — 빌드 한 번이 약 1.3GiB 까지 쓸 수 있다고 본다.
- 계약: `tests/test_build_manifests.py`(봉투 2Gi), `tests/test_portal_image_build.py`(힙 고정·힙 ≥ 768MB·상한 − 힙 ≥ 768MB). 문서: deploy/README §0-2 자원
  표·§8 3c, frontend/README, BACKLOG(여유 항목 해소).
- 반영 경로: 빌드 파드 매니페스트는 컨트롤러의 build-watcher(`build_runner.build_build_pod`)가 만든다 — 새 상한은 이 커밋을 담은 dms 이미지를 컨트롤러에 **릴리스한 뒤의 빌드**부터 적용된다(그 전 빌드는 옛 1Gi 지만 힙 1024MB 고정·`build:image` 는 RSS 약 630MB 라 그 안에서 돈다).
- **라이브 실증**: ① d42 빌드(소스 7953c78d — 아직 옛 컨트롤러 d41 이 만든 1Gi 파드)가 힙 1024MB 고정으로 성공(195초) → 릴리스 4종 Applied(컨트롤러
  d42) → 오버레이 d41 → d42. ② 검증용 d43 빌드(같은 소스 — 릴리스 안 함): 빌드 파드 `dms-build-babdf84a6da1`(k8s-w1) 상한 `memory: 2Gi`·
  requests 128Mi 그대로, 성공(225초). 빌드 중 cgroup `memory.current` 최대 1479MiB — buildah 레이어 쓰기의 페이지 캐시 포함이라 프로세스 메모리가
  아니다(상한에 다가가면 커널이 캐시를 먼저 회수한다 — 1Gi 의 d42 가 성공한 이유). 테스트베드 커널(Rocky 9, 5.14)엔 `memory.peak` 가 없다.

### ✨ API 파드 자체 아티팩트 경로 점검·시작 게이트·「base 접근 불가」 503 사유 + 관리 화면 API/컨트롤러 분리 + 마운트 현황 DMS 파드 꼬리표(dms-ssc 4add57bf 이식) — **완료·실증**(2026-11-23, d40 → d41)

사용자 요청(2026-11-23): 프로덕션 사고 — dms-api 가 CephFS 미마운트 노드(`/mgmt_storage` 가 로컬 빈 디렉터리)에 떠 모든 잡의
아티팩트·scan 리포트를 「없음」으로 보였는데, 아티팩트 경로 화면의 base 점검은 컨트롤러 파드(마운트된 다른 노드) 기준이라 정상이었다 —
의 코드 방어. 목표 넷: ① API 파드 자체 base 점검 기록·노출(컨트롤러 점검 유지) ② 시작 점검 실패면 Ready 안 됨(롤링 때 옛 파드 유지),
런타임 일시 실패로는 죽이거나 내리지 않음 ③ 「base 불가」와 「아티팩트 없음」 구분(별도 사유 코드·포탈 문구·TargetDetail 원인, 일시
장애를 scan_report_digests 등에 영구 저장 금지) ④ 관리 화면에 컨트롤러·API 점검 분리 표시. 사용자: 「dms-ssc 에서 검토·구현 중이니 완료
후 동일하게 진행, 검증은 필수」 — dms-ssc 가 origin/dms-ssc `4add57bf` 로 먼저 구현했고 이 항목은 그것을 `cherry-pick -n` 으로 가져와
dms-k8s 에 맞춘 것이다.

**가져온 것(dms-ssc 설계 그대로 — 코드·모듈 docstring 이 진실, 요지는 ARCHITECTURE §7 불변식 16·deploy/README §12e)**:
- 판정 `artifact_base.availability_check` — 쓰기 없음: base 디렉터리 열기·첫 dirent·**최근 성공 잡 디렉터리**(DB 가 고른 최대 5건의
  `<base>/<job_id>` fstatat — 하나도 없으면 `artifact_base_jobs_missing` = 빈 로컬 디렉터리의 서명; 실 실행 백엔드에서만, 마지막 base
  변경 뒤 만든 잡만). 센티넬 파일 없음. FS 점검은 프로세스 단일 `BaseCheckRunner`(single-flight·10초 타임아웃·짧은 캐시) 안에서만.
  mountinfo 파서는 중립 `mountinfo.py` 로(agent.probes 가 재수출).
- API 모니터 `api/base_monitor.py`(`dms api` 만 start) — 60초(첫 성공 전 5초·실패 중 15초) → 메모리 스냅숏·`api_base_checks`(파드별
  1행·새 테이블)·이벤트 `api-artifact-base`. `/readyz` 시작 게이트 = (첫 회차 미완료 ∧ 60초 이내) ∨ (실패 ∧ 서비스 중인 건강한 형제
  dms-api 파드) — 홀로 뜬 파드는 막지 않고, 한 번 Ready 면 다시 안 막는다. 게이트 503 은 자기 종료 카운터와 무관. 비상구
  `DMS_API_ARTIFACT_BASE_STARTUP_GATE=0`·`DMS_API_ARTIFACT_BASE_CHECK_SECONDS`(관대 파싱). `/healthz` async.
- 읽기 실패 분류(`api/base_probe.BaseProbe`): 아티팩트 목록·보기·다운로드·scan-stats·scan-path-stats 는 base 수준 판정이 실패일 때만
  503 `artifact_base_unavailable`(파일별 errno 로 가르지 않는다 — 존재 오라클), 멈춘 마운트는 파일을 열기 전에 503. 사용량은 base 불가면
  남은 리포트를 열지 않고 `unreadable_reason` 을 싣고 실패를 `scan_report_digests`·`ReportReadFailures` 에 넣지 않는다(pending).
- stepper: `summary_unavailable` 영구 기록을 컨트롤러 쪽 base 불가인 동안 min(20분, vcjob TTL/2) 미룬다(`_defer_summary`).
- 관리 API: `checks.api` 새 모양(가용성·posture·노드·파일시스템·시작 게이트)·`checks.api_pods`·컨트롤러 점검 노드
  (`control_state.artifact_base_check_node`)·`GET …/artifact-base/health`·`POST …/artifact-base:recheck`·metrics/infra `artifact_base`, API 홉
  쓰기 왕복 제거(PUT/validate 는 그대로). 포탈: 사유 코드 4종(reasonCodes.json + api.ts 양쪽), 작업 단계·OutputViewer·ItemScanStats·사용량
  원인 문구와 복구 감시(health → 한 번 무효화), 아티팩트 경로 화면 API/컨트롤러 두 패널·파드 카드·엇갈림 상자·「지금 다시 점검」, 대시보드
  경고 줄.
- 매니페스트(공용 base): 40-api `strategy` RollingUpdate maxSurge 1 / maxUnavailable 0 명시(게이트 이점의 전제 — 계약 테스트)·
  `DMS_NODE_NAME`(downward), 41-controller `DMS_NODE_NAME`.

**이식 결정**:
- **마운트 화면은 dms-k8s 것을 유지**한다 — dms-ssc 의 `GET /api/admin/mounts`(routes_mounts)·`MountsPage`·`mountView`·`useMounts`·
  `/admin/mounts` 라우트·사이드바 항목은 가져오지 않았다(이 브랜치엔 같은 목적의 「스토리지 → 마운트 현황」 `/admin/storages/mounts` ·
  `GET /api/admin/mount-status` 가 먼저 있다). 아티팩트 경로 화면의 「마운트 현황」 링크는 `/admin/storages/mounts` 로 바꿨다.
- 런북 절: dms-ssc §12b → 이 브랜치 **§12e**(§12b 는 결과 파일 자동 만료, §12c·§12d 는 은퇴 번호 — 부록 Z). prod 오버레이 README 의 운영 색인에
  §12e 한 줄.
- 40-api 헤더의 DirectoryOrCreate 금지는 이 브랜치의 한국어 문장(2026-11-03 ③)을 유지하고 계약 테스트 단언을 그 문장으로(dms-ssc 의 영어
  「NEVER switch to DirectoryOrCreate」 대신). stepper 의 큐 뒤로 밀기는 이 브랜치의 `touch_held`(dms-ssc `touch`).
- dms-k8s 전용 소속별 사용량(`/api/admin/usage/breakdown`·BreakdownPage)에도 같은 `unreadable_reason`·원인 줄·복구 감시를 넣었다(스토리지별
  TotalsLine·StoragesPage 포함 — 사용량 세 화면이 같은 규칙).
- LDAP 관련 코드는 들여오지 않았다(k8s 신원 모드 전용 — 해당 hunk 없음 확인).

**확장 — 마운트 현황에 DMS 파드 꼬리표**(`mount_status_pods`, 두 라인 공용 재료 — BACKLOG 💡 「API·컨트롤러 파드가 뜬 노드 꼬리표」 해소):
재료는 k8s 파드 목록이 아니라 DB(`api_base_checks` 신선 행·`control_state` 의 컨트롤러 점검 노드 — 화면이 10초마다 묻는데 요청마다 k8s API 를
부르지 않는다). 행 `roles` 에 `api_pod`·`controller_pod`(포탈 「dms-api」·「컨트롤러」), 최상위 `control_pods`(파드별 점검·시작 게이트 상태).
공용 디렉터리 칸이 문제인 노드거나 그 파드의 경로 점검이 실패하면(권한 사유 제외) 꼬리표가 경고(빨강 — 사고 서명), 그런 행은 「문제 있는
노드」에 든다(DMS 공용 열 한 칸 — dms-ssc routes_mounts 와 같은 규칙). 표 밖 노드(prod 마스터 — 에이전트 DaemonSet 이 control-plane taint 를
견디지 않는다)의 파드는 각주. 신선 창은 관리 화면 API 파드 목록과 같은 `fresh_window_seconds`.

**리뷰 반영**(같은 날, 이식 diff 리뷰):
- **만료 잡을 표본에서 뺀다**(dms-k8s 고유 — dms-ssc 엔 만료가 없어 diff 대조로는 안 보이는 것). `expire_artifacts` 는 행·상태·
  artifact_uri 를 남기고 `artifacts_expired_at` 만 찍은 뒤 정리 루프가 `<base>/<job_id>` 를 trash 로 떼어 내므로, 보존일
  (`DMS_ARTIFACT_RETENTION_DAYS`) 안에 성공 잡이 없던 사이트에서 멀쩡한 base 가 `artifact_base_jobs_missing` 이 됐다(거짓 503·사용량
  멈춤·요약 지연, 그리고 이 버전으로 올리는 롤링에서 새 dms-api 가 게이트에 막혀 릴리스 시간 초과). `recent_job_ids` 가
  `artifacts_expired_at` 을 함께 읽고 파이썬에서 거른다(SQL WHERE 로 넣으면 전부 만료인 사이트에서 Postgres 가 성공 잡 인덱스 전체를 훑는다 —
  창 `RECENT_JOB_WINDOW` 로 비용 일정 유지, 질의 계획 테스트 그대로). 테스트 `test_recent_job_ids_skip_jobs_whose_artifacts_expired`.
  dms-ssc 로 되돌려 보낼 때는 그쪽에 칸이 없으니 이 거름은 빼고.
- 파드 꼬리표 문구 「아티팩트 경로 읽기 실패」 → 「아티팩트 경로 **점검** 실패」 — `read_failed` 에는 컨트롤러 쓰기 왕복 실패
  (`artifact_base_not_writable`)·점검 시간 초과도 든다(무엇이 실패했는지는 사유 문장이 말한다). `mount_status_pods` docstring·테스트 단언 추가.
- deploy/README §12e: 이 버전 뒤 **첫 S11** 이 40·41 파드 템플릿 변경으로 api·controller 를 한 번 더 롤링한다(게이트 포함 — 시간 초과면 「Ready
  안 되는 새 파드」), 멈춘 마운트 감지 전 창(최대 ~70초)에서 readyz 가 스레드풀 고갈로 굶을 수 있다(복구 뒤 `rollout restart`), 컨트롤러 노드의
  base 가 비면 삭제·만료 정리가 파일을 못 지운 채 끝날 수 있다(아래 남은 것) — 세 문단.
- 잡·preflight 파드 hostPath 가 모두 `type: Directory` 임을 고정하는 테스트(`test_every_job_and_preflight_hostpath_is_type_directory` — dict 엔
  type 이 없고 `execution_manifests._pod_volumes` 가 붙인다; 그 type 을 지운 뮤테이션에서 빨개짐 확인). 잡 파드 hostPath 는 사고 원인이
  아니다(무타입 아님).

**남은 것**(BACKLOG §4 「API 자체 artifact base 점검 후속」): 🐞 **(최우선) 미마운트 노드 컨트롤러의 정리 루프가 「없음 → 완료」** — 요청·배치
삭제·회차 정리·만료가 빈 base 에서 정리 행을 끝내 공유 스토리지의 진짜 디렉터리가 고아가 된다(dms-ssc 와 공통 — 가드 모양은 BACKLOG,
사용자 지시대로 dms-ssc 선행 → 동일 이식). 📝 멈춘 마운트 감지 전 readyz 굶음. 그 밖에 미리보기 단계 summary None 의 같은 굳음·릴리스 실패
사유에 게이트 정보(dms-ssc 원문 후속).

**검증**(로컬): 백엔드 전체 6255 passed · 19 skipped(595초, TMPDIR·basetemp /dev/shm), vitest 2209 passed(130 파일)·`npx tsc -b` 0·
`npm run build:image` 통과(`dist/index.html` 외부 URL 0건), e2e 41 passed(19 spec — 빌드 포함). 새·바뀐 테스트: `test_artifact_base_availability`·
`test_api_base_monitor`·`test_api_artifact_base_unavailable`·`test_api_usage_base_unavailable`·`test_stepper_summary_deferral`·`test_mount_status_pods`·
`test_release_manifest_contract`(strategy·DMS_NODE_NAME·DirectoryOrCreate 금지)·`test_execution_volcano`(hostPath type)·`test_migrations`(`api_base_checks`)
+ vitest ArtifactBasePage·Dashboard·JobStages·OutputViewer·UsageAnalysis·UsageBaseDown·useUsageBaseRecovery·mountTerms·MountStatusPage.

**dms-ssc 후속 68dcbcdb 반영**(그쪽 d201 실증에서 찾은 모순 둘, 같은 원칙): ① 아티팩트 경로 화면 머리 배지(`hopSummary`)가 응답한
파드만 세어, 롤링 중 Ready 를 기다리는 새 파드가 실패해도 「모두 정상」이었다 → 다른 API 파드(fresh)의 실패도 센다(보고 끊긴 파드 제외).
② 마운트 현황의 공용 칸이 에이전트 「정상」(초록)인데 그 노드의 dms-api·컨트롤러 점검은 실패 — dms-k8s 화면은 이미 그 행을 「문제 있는
노드」로 세고 파드 꼬리표를 경고로 바꿨지만 칸은 초록으로 남았다 → `mountTerms.sharedDisplay`: 이 노드 파드 중 경로 점검 실패
(read_failed — 권한 사유만이면 아님)가 있고 에이전트 판정이 문제·지연이 아니면 칸을 「파드 점검 실패」(빨강)로, 에이전트 판정은 보조 줄·
툴팁의 「에이전트 보고」로(표·카드·노드 시트 공통). dms-ssc판 마운트 화면 파일(MountsPage)은 가져오지 않았으니 그 부분은 dms-k8s 화면에
같은 규칙을 새로 쓴 것이다.

**배포·라이브 실증(d40)**: 포탈 빌드 d40(소스 e4aed4b3, 3종, 210초) → 릴리스 dms-agent → dms-api → dms-controller → job-image 모두 Applied
(새 dms-api 는 첫 회차 점검을 통과해 바로 Ready) → 오버레이 d39 → d40(9b7a7b2e) → 설치기 `--only S11`(PASS — 40-api strategy RollingUpdate
1/0·DMS_NODE_NAME, 41-controller DMS_NODE_NAME 반영, 그동안 d35 에 남아 있던 ConfigMap `DMS_JOB_IMAGE` 도 d40 으로 맞춤, api·controller·에이전트
한 번 더 롤링). 실 Chrome(헤드리스, 인증서 오류 무시):
- 정상: checks.api ok·nfs4·노드 k8s-w1·시작 점검 통과, 컨트롤러 ok·k8s-w1, health ok, readyz 200. 아티팩트 경로 「모두 정상」·두 패널, 마운트
  현황 w1 에 「dms-api」·「컨트롤러」 꼬리표.
- 사고 재현(롤링): dms-api 의 `dms-artifacts` 를 JSON 패치(test + replace)로 emptyDir 로 → 새 파드(k8s-w1)가 154초 동안 0/1·재시작 0(readiness
  503 15회, liveness 실패 0), 옛 파드 1/1 로 계속 서비스(포탈 아티팩트 목록 200). 옛 파드의 관리 화면: 새 파드 실패 —
  `artifact_base_jobs_missing`(표본 5건 중 0건)·xfs(로컬로 보임)·「Ready 대기 중」, 머리 배지 「문제 1건」·엇갈림 상자. 마운트 현황: w1 「문제 1」·
  경고 꼬리표 「⚠ dms-api ×2」(툴팁 파드별 정상 / 실패·Ready 대기), 공용 칸 「파드 점검 실패 / 에이전트: 정상」, 문제 노드 1·`?only=problems` 에
  w1 만. 새 파드 직접(port-forward + 관리자 쿠키): 아티팩트 목록·파일 보기·scan-stats 503 `artifact_base_unavailable`, health `ok:false
  jobs_missing`. 이벤트 `api_artifact_base_unavailable` 는 전이 때 1건. (scan-history 는 리포트 요약이 전부 `scan_report_digests` 캐시에 있어
  base 를 읽지 않고 200 — 설계대로.)
- 원복: 역패치(test 포함, rollout undo 아님)로 원래 RS 재사용·원래 파드 그대로(재시작 0) → spec.template 이 사전 기록과 같음(키 정렬 비교), readyz
  200. 지운 실패 파드의 점검 행은 신선 창(180초) 동안 경보를 유지하다 「보고 끊김」이 돼 집계에서 빠졌다(BACKLOG). 회귀 화면 콘솔 0·4xx/5xx 0.
- 실증에서 찾은 문구 셋(d41 에서 고침): ① 엇갈림 상자가 실패한 API 파드와 정상인 컨트롤러·옛 API 파드가 **같은 노드**인데도 「노드 k8s-w1 에 공유
  스토리지가 마운트돼 있는지」라고 했다 → 실패 노드마다 정상 파드가 함께 있으면 「노드 마운트가 아니라 그 파드의 hostPath·마운트 전파」. ② 대시보드가
  시작 게이트에 막힌(서비스 밖) 새 파드에도 「그 파드로 간 조회는 …」이라고 했다 → metrics/infra 에 `api_pods_failing_waiting` 을 더해, 대기 파드뿐이면
  「새 API 파드 N개가 … Ready 를 기다립니다(서비스 밖) — 롤아웃이 멈춰 있으니 …」. ③ 공용 칸 「파드 점검 실패」 도움말이 고정 「dms-api·컨트롤러」 →
  실패한 역할 이름(`podFailedRoles`·`podFailedHelp`). ①②는 dms-ssc 4add57bf 에서 온 문구라 dms-ssc 에도 같다.
- **d41 재확인**(빌드 d41 소스 e25a2dfb → 릴리스 4종 Applied → 오버레이 d40 → d41 f16768ef, 같은 사고 재현 42초 → 역패치 원복): 새 파드가 다시
  k8s-w1(컨트롤러·옛 API 파드와 같은 노드)에 떠 ① 엇갈림 상자가 「같은 노드의 다른 DMS 파드(컨트롤러 노드 k8s-w1)는 정상 — 노드 마운트가 아니라
  그 파드의 hostPath·마운트 전파…」(옛 「공유 스토리지가 마운트돼 있는지」 없음), ② metrics/infra `api_pods_failing_waiting` 1·대시보드 「새 API
  파드 1개가 … Ready 를 기다립니다(서비스 밖 — 옛 파드가 계속 서비스) …」, ③ 공용 칸 툴팁·시트 「이 노드의 dms-api 파드가 …」(컨트롤러 언급
  없음). 원복 뒤 spec.template 동일·readyz 200·회귀 화면 콘솔 0.

### 🔧 포탈 이미지 빌드의 프런트 타입 검사 heap OOM — 이미지 빌드는 앱 코드만 타입 검사 — **수정 완료**(2026-11-23)

d37 포탈 빌드(소스 d14ee402 — 마운트 현황)가 `RUN npm run build` 의 `tsc -b` 에서 `JavaScript heap out of memory`(exit 134)로 실패했다.
빌드 파드 memory limit 1Gi(`build_manifests` — 노드보다 빌드가 먼저 혼자 죽게 하는 의도된 1차 방어)의 Node 힙 자동 상한은 약 505MB 다.
`tsc -b`(tsconfig.json — `src` 전부, 테스트 128개 파일 포함)는 힙 510~550MB 가 필요해 d36 까지 겨우 들어가던 것이 이번 화면 추가로
넘었다. 로컬 재현: `--max-old-space-size=500` 이면 테스트 포함 rc 134, 앱 코드만이면 통과(350 에서 실패 — 필요 약 400MB, 여유 약 25%).

- 이미지 빌드만 `npm run build:image` = `tsc -p tsconfig.build.json && vite build`(새 `frontend/tsconfig.build.json` — 테스트·테스트 전용
  파일 제외, vitest 전역 타입 없음). 테스트 타입 검사는 그대로 개발 게이트 `npx tsc -b` 와 vitest 다. 로컬 `npm run build` 는 바꾸지 않았다.
- 1Gi 상한은 올리지 않았다(노드 보호 설계 — 위 「1차 방어」). 앱이 계속 커져 다시 넘으면 그때 상한·힙(`NODE_OPTIONS`)을 함께 정한다(BACKLOG).
- 계약 테스트 `tests/test_portal_image_build.py`(Dockerfile 이 `build:image` 를 쓰는지·스크립트·테스트 제외 패턴).

### ✨ 스토리지 > 마운트 현황(노드 × 스토리지 매트릭스) + 에이전트 공용 디렉터리 덮는 마운트 보고 — **완료·실증**(2026-11-23, d38)

사용자 요청(2026-11-23): 「노드들의 스토리지 마운트 현황을 한눈에 보고 싶어. 스토리지 하위에 마운트 현황 메뉴를 추가하고 테이블로
노드 - 스토리지 마운트 현황 예쁘게 보여지게 해줘. … dms-k8s 는 pv 및 k8s 환경도 고려해야 되니까 잘 고민해서 동일하게 갈지 별도로
구현 할지는 너가 선택해서 진행해.」 같은 날 사고(공용 스토리지가 마운트되지 않은 노드에 dms-api 가 떠 아티팩트를 못 읽음)의 그런
노드를 한눈에 찾는 도구이기도 하다.

**결정 — 공용 코어는 dms-ssc 와 같게, k8s 정보는 별도 파일로 덧붙인다.** 칸 판정의 재료(에이전트 mounts·artifact_base, reconciler)가
두 라인에서 바이트 단위로 같아 판정 규칙도 같아야 한다(따로 지으면 두 화면이 갈라진다). PV 를 따로 다룰 근거가 없다: 잡 파드는 PVC 를
CSI 로 붙이지 않고 스토리지 mount_path 를 hostPath 로 붙여 PV 디렉터리에 접근하므로 칸 하나가 그 노드에서 그 스토리지의 PV 전부에
접근 가능한지를 말하고, 에이전트는 PV 별 점검을 하지 않는다. k8s 덧붙임은 스토리지 열의 종류·지금 고를 수 있는 PV 수·StorageClass·
클러스터와 노드 행의 k8s 호출 노드(주·보조)뿐이다.

**에이전트**: `probe_artifact_base` 가 공용 디렉터리를 덮는 마운트 지점·fstype 을 더 싣는다(호스트 루트 모드 — direct·못 찾음은 null).
exists·writable 만으로는 공유 마운트가 빠지고 같은 경로의 로컬 디렉터리만 남은 노드가 정상으로 보였다(덮는 마운트 '/' 가 rw). 반영은
에이전트 롤아웃 뒤 — 그 전의 옛 에이전트는 「있음(공유 여부 모름)」으로 정직하게 보인다.

**백엔드**: `GET /api/admin/mount-status`(require_admin) — 공용 코어 `mount_status`(순수, k8s·PV·LDAP import 0건 AST 그물) + dms-k8s
`mount_status_k8s.decorate` 한 줄. 칸 상태 ready·read_only·absent(경로 없음 — 안 붙인 노드면 정상이라 문제로 세지 않음)·broken·
pending(보고 없음·경로 바뀜 — 에이전트는 직전 응답 목록으로 점검)·stale, 공용 디렉터리 ok·ok_unverified·local·differs(신선한 ok 행의
엄격한 과반과 다름)·missing·read_only·pending·stale. 열 머리 요약은 reconciler 에서 추출한 `summarize_mount_statuses` 를 공유한다(스토리지
목록 배지와 같은 숫자 — 교차 검사 테스트). k8s 노드 목록은 30초 메모·fail-soft 로 에이전트 없는 노드(control-plane)·「클러스터에 없음」
(퇴역 노드의 옛 보고)을 보인다. 경로가 `/api/admin/storages/mounts` 가 아닌 것은 스토리지 이름 `mounts` 와 모호해서. 새 HTTPException 사유
없음(reasonCodes 변경 없음).

**포탈**: 사이드바 스토리지 그룹 「스토리지」 바로 아래 「마운트 현황」(`/admin/storages/mounts`, Grid3x3). 요약 타일 넷(노드·마운트 문제·
읽기 전용·공용 디렉터리), 보기 범위(전체/문제 있는 노드)·보기 방식(표/노드별) — URL 에 남는다, 범례(칸과 같은 칩 + 개수), 노드 × 스토리지
표(고정 머리·첫 열, 래퍼 `isolate` 로 z 를 가둬 시트·툴팁 포털 아래), 칸에 올리면 즉시 툴팁, 노드 버튼 → 측면 시트(닫으면 그 버튼으로
포커스), 좁은 화면은 노드별 카드(조건부 렌더 — 문제 칸 먼저, 정상은 접음), 각주(비활성 스토리지·에이전트 없는 노드·노드 목록 모름·
마운트 지점 모름·배치는 노드 화면). 칸은 아이콘 + 글자 + 면색, 마운트 사유는 `mountTerms` 로컬 맵(원문 코드는 툴팁·시트 끝).
공용 코어 8파일은 `features/k8s` 를 import 하지 않는다(그물 `mountTerms.test`) — k8s 덧붙임은 `k8sMountParts`(K8sMountStatusPage 슬롯 셋).
`NodePlacement` 의 배지 함수는 인자 타입만 넓혔다(투영 행을 넘긴다 — 동작 불변).

**ssc 이식 메모**: 코어 파일 — 서버 `reconciler.summarize_mount_statuses`·`mount_status.py`·`api/routes_mount_status.py`(decorate 한 줄만
지움)·`agent/probes.py` 의 `probe_artifact_base`, 포탈 `features/mounts/` 의 types·useMountStatus·mountTerms·MountCell·MountMatrix·
NodeMountCards·NodeMountSheet·MountStatusPage(+ 테스트·픽스처), 경로 `/admin/storages/mounts`, API `/api/admin/mount-status`, 응답 모양
동일. ssc 는 `mount_status_k8s.py`·`k8sMountParts.tsx` 를 빼고, 라우터는 `MountStatusPage` 를 바로 쓴다. ssc 가 다른 모양으로 먼저 들어가면
공용 코어를 그쪽 계약에 맞추고 덧붙인 파일만 유지한다.

**검증**: 백엔드 새 테스트 `test_mount_status.py`(칸·공용·differs·메타·totals·reconciler 교차 검사·이식 경계)·`test_mount_status_k8s.py`·
`test_api_mount_status.py`(권한·응답 계약·노드 목록 메모·fail-soft·실 인벤토리 PV 수·미적용 클러스터 = null) + 에이전트·reconciler 기존
테스트 갱신. vitest `mountTerms.test`·`MountStatusPage.test`·`k8sMountParts.test` + navigation(21링크)·router·AppShell. e2e E19
(`19-mount-status.spec.ts`, 고정 칸이 측면 시트·덮개 아래 — `isolate` 를 지운 뮤테이션이 1280·375 둘 다 빨개짐을 확인; 모달이 뒤 화면에
거는 pointer-events: none 을 잴 때만 되돌려야 쌓임 순서가 재진다) + E3(03-layout) 1280·375 순회에 표·카드 보기.

**리뷰 반영**(같은 날): ① 읽기 전용 칸은 같은 스토리지를 다른 신선한 노드가 쓰기 가능(ready)으로 붙였을 때만 「문제 있는 노드」로 센다
(이관 소스를 모든 노드에 ro 로 붙인 구성이면 필터가 전체와 같아졌다 — absent 를 세지 않는 것과 같은 이유; 칸·집계의 읽기 전용 수는
그대로). 행에 `problem_count`(지연 행은 null — 모름)를 더해 표의 고정 첫 열·노드별 카드 머리에 「문제 n」·「지연」 꼬리표(아이콘 + 글자). ② 쓰기
여부 「불가(읽기 전용)」는 붙어 있는 칸(정상·읽기 전용, 공용은 exists)에서만 — 에이전트는 경로 없음·마운트 지점 아님에도 writable=false
라 그 칸을 읽기 전용이라 말하던 것을 「불가」로. ③ 리포트 지연 흐림 text-ink/50(2.8:1) → /70(약 4.9:1), 노드 이름은 흐리지 않음. ④ 열 머리
bg-surface + 스토리지·공용 열 가운데 정렬(중립 꼬리표가 머리 면색에 묻혔다), 범례 수에 「칸」 단위, 공용 타일 줄바꿈, 「SC 매핑 없음」 →
「StorageClass 매핑 없음」(기존 화면 용어 + 설명).

**배포·라이브 실증(d38)** — d37 은 포탈 빌드가 프런트 tsc heap OOM 으로 실패해 건너뛰었다(바로 위 「포탈 이미지 빌드의 프런트 타입 검사
heap OOM」 — 7c067d85). 포탈 빌드 d38(소스 7c067d85, 3종, 195초) → 릴리스 dms-agent → dms-api → dms-controller → job-image 모두 Applied
(live == 동봉 d38) → 오버레이 d36 → d38(0e3c48c9). 실 Chrome(헤드리스 Chrome 147, 테스트베드 CA 대신 인증서 오류 무시):
- API: 2행(k8s-w1·k8s-w2) × 6열, 칸 정상 10·없음 2(w1/cephfs-secondary·w2/cephfs-third — 안 붙인 노드, 문제 아님)·문제 노드 0. 공용 칸 두
  노드 모두 정상(덮는 마운트 `/dms_shared`·`nfs4`, 과반 2/2 — d38 에이전트 원 보고에도 mount_point·fstype). 에이전트 없는 노드 k8s-cp1
  (control-plane) 각주. 열 메타 k8s-pv-gpfs 둘 다·PV 2개 / k8s-pv-nfs PV·5개·클러스터 2 / k8s-pv-onefs PV·1개, cephfs 셋 파일시스템. 꼬리표 w1
  빌드 노드 + k8s 호출 주, w2 k8s 호출 보조. 스토리지 목록 6개의 status·status_detail = 열 머리 요약.
- 화면 1280: 사이드바 음영 「마운트 현황」만, 요약 타일·범례 단위, 고정 머리·첫 열, 칸 아이콘 + 글자, 툴팁(사유·경로·점검·마지막 보고·사유
  코드), 노드 버튼 → 시트 → 닫기·Esc 뒤 포커스 복귀, 「문제 있는 노드 0」 빈 상태. 390: 노드별 카드 기본·가로 넘침 0, 표 보기는 래퍼 안에서만
  스크롤. 마운트 현황·스토리지·노드·대시보드·작업 제출 콘솔 0·실패 요청 0. (다크 모드는 앱에 정의가 없다 — 해당 없음.)
- 사고 유형 탐지: 가짜 노드 `proof-localdir` 보고(mounts 빈 목록 — reconciler·배치 영향 없음을 코드로 먼저 확인, artifact_base
  mount_point `/`·fstype xfs)를 넣자 그 행이 「로컬 디렉터리」(툴팁 덮는 마운트 `/ (xfs)`)·「문제 1」·「클러스터에 없음」, 「문제 있는 노드 1」
  필터에 그 행만, 공용 타일 「정상 2/3대 · 문제 1대」, 스토리지 상태 6개 불변. 정리: agent_nodes·agent_reports 의 그 행을 프라이머리(dms-pg-2)
  에서 한 트랜잭션으로 지움 → 세 DB 파드 0건, 화면·API 2행.
- 실증에서 찾은 것(다음 커밋에서 고침): ① 노드 시트의 스토리지별 6열 표가 640px 시트에서 경로 열을 38px 로 찌그러뜨려 「/c ep hf s」처럼
  끊기고 사유 열은 가로로 밀어야 보였다 → 표 대신 세로 쌓임 목록(줄 = 스토리지: 이름·상태 칩 / 경로 / 쓰기·사용량 / 사유). ② 사이드바
  `aria-current="page"` 가 음영(최장 일치)과 별개로 NavLink 접두 일치마다 붙어 「스토리지」까지 현재 페이지로 읽혔다(/jobs/new 의 /jobs 도
  같은 기존 패턴) → 같은 판정 하나(activeNavPath)로 단다. ③ 붙지 않은 칸(없음·마운트 문제)의 쓰기 표시 「불가」 → 「—」(쓰기 여부를 따질
  수 없다).

### 🔧 배치 단위 삭제 상한 기본 50000 + 청크(백그라운드) 삭제 + 삭제 화면 상한 표시·「삭제 불가」 — **완료·실증**(2026-11-19, d35 → d36)

사용자 결정 둘(2026-11-19): 「배치 단위 삭제 상한 기본 50000개로 올려줘.」 · 같은 날 작업 중 「포탈에서 상한 값도 삭제 시 보여지고 값을
넘어서는 요청에 대해서 삭제 불가 메시지 띄우게 해줘」. 설계 원문은 job 산출물(`cap50k/DESIGN.md` — D1–D14·자기 비판 C1–C15).

**왜 한 트랜잭션을 버렸나**(조사, 설계 §2): 지금의 `delete_batch` 로 현실 모양 자식 5만을 지우면 로컬 PG16 20.9–22.9초·테스트베드 환산
35.9–42.9초 동안 API 단일 커넥션 RLock 을 쥐어 readiness(10s/1s/×3)를 3–5회 연속 놓치고(NotReady — 포탈 503) RSS +674MiB 다.

**백엔드**
- 상한은 설정 키: `DMS_BATCH_DELETE_MAX_CHILDREN`(기본 50000, 1–50000 — 밖이면 기동 거부, 모듈 상수 `MAX_BATCH_DELETE_CHILDREN` 삭제)·
  `DMS_BATCH_DELETE_SYNC_MAX_CHILDREN`(기본 500, 0–1000). base `20-config.yaml` 에 기본값과 같은 값.
- 진입점 `RequestPurgesRepository.delete_batch_auto`: 작은 배치(≤ 문턱, 호출의 동기 합 ≤ 2 × 문턱)는 기존 `delete_batch` 한 트랜잭션(자식별
  감사 그대로 — 세기 직후 자식을 잠그기 전에 CAS 를 보는 「일찍 CAS」, 최대 commit_order CAS 선택 인자, 항목 초과는 새 사유
  `batch_delete_too_many_items`, 상한 사유는 `limit`·`count`·`count_at_least` 를 싣는다). 큰 배치는 A단계 `batch_delete_check`(트랜잭션 밖,
  같은 사유 순서 — 패리티 테스트) → B단계 묘비 `_begin_batch_delete`(배치 상태 `Deleting` + 새 테이블 `batch_deletions` + 회차 경계·스케줄·
  회차 이력 삭제 + `('batch','delete_start')`) → 컨트롤러 새 루프 `batch-delete`(`batch_deleter.py` — 5초, 틱 예산 2초, 조건 없이 조립):
  `delete_batch_chunk`(≤ 100 자식 = 한 트랜잭션, 잠근 행으로 전부 재판정, `_purge_locked(per_request_audit=False, purge_group=batch_id)` +
  `('batch','delete_chunk')` 요약) → `finish_batch_delete`(배치 삭제 셋째 경로 — `('batch','delete')` 배치 스냅숏). 막히면 「삭제 지연」
  (`defer_batch_delete` 15→900초 백오프, 사유가 바뀔 때만 이벤트). 커서·진행·사유는 묘비 행에 영속.
- 잠금 순서 하나: `batch_items → batches → batch_deletions → requests → data_jobs → batch_rounds → batch_schedules → batch_schedule_runs`
  (`BATCH_DELETE_TABLES` 에 `batch_deletions`, 세 경로 모두 DELETE — PG 잠금 순서 그물에 묘비·마무리 추가).
- 묘비 뒤 되살림 금지: 라우트 `_reject_frozen`(409 `batch_deleting` — 항목 추가·수정·삭제·CSV 교체·재실행·이름·실행 설정·`:rescan`·기록
  삭제) + 저장소 원자 가드(sentinel·`update_meta` 0행·스케줄 무장 `DomainValidationError`), 스케줄러 판정은 `Deleting` 을 건너뛴다.
- 정리 아웃박스 레인: `request_purges.purge_group`(CREATE + `_ensure_columns`), `due()` 가 `ROW_NUMBER() OVER (PARTITION BY
  COALESCE(purge_group, ''))` 로 돌아가며 집는다.
- API: `POST /api/admin/requests:delete` 응답에 `deleting_batches[{batch_id, request_count, already}]`, 호출 합 예산 10000 → 100000(상수),
  배치 트랜잭션 사이 2ms 양보, 본문 `expected_max_commit_order`(strict). 새 `POST /api/admin/requests:delete-preview`(require_admin — limits +
  배치마다 정확한 수·최대 commit_order·항목 수·판정 sync|async|blocked|deleting|gone). `GET /api/admin/request-purges` 에 `limits`·
  `batch_deletions`. 목록 요약은 min(5000, 상한)에서 세기를 멈추고(폴링 비용 그대로) `batch_max_commit_order`·`batch_deleting` 을 싣는다.
  배치 상세 `deleting`. 스케줄 `child_count_limit` = 설정값(목록은 성공 scan 갈래 생략). 미등록 스토리지 정리도 같은 진입점.
- 새 사유 `batch_delete_too_many_items`·`batch_deleting`(양쪽 등록). 상한 사유 문구에서 숫자를 뺐다(설정값이라 — 숫자는 포매터).

**포탈**(추가 요청)
- `lib/deleteLimits.ts`(`parseLimits`·`limitsLine`·`blockText` — 미리 판정과 서버 거절이 같은 포매터), `lib/format.ts`(`n0`).
- 작업 목록 툴바에 늘 있는 상한 줄(「한 번에 작업 200개·배치 10개 · 배치당 작업 50,000개·항목 10,000개 · 합계 작업 100,000개」 — 서버
  값). 선택이 상한을 넘으면 같은 자리가 role=alert 「삭제 불가 — 작업 N개가 한 번에 지울 수 있는 상한 M개를 넘습니다」로 바뀐다(예약
  높이·두 줄 접기 — 툴바 높이 불변). 상한 초과 배치는 「삭제 불가·상한」이지만 **고를 수 있다**(조용히 잠그지 않음), 트리거도 잠그지
  않는다. 상태 줄 꼬리는 「· 삭제 불가」로 짧게. 호출 합 판정은 서버처럼 첫 배치를 늘 통과(예전 어긋남 수정).
- 확인 창: 열 때 미리보기 → 상한 상자·판정 칩(바로 삭제·백그라운드 삭제·이미 삭제 중 N%·이미 지워짐·삭제 불가)·문제 상자(role=alert)·
  제출 잠금(로딩 중 포함), 보내는 expected = 미리보기 수 + `expected_max_commit_order`, 「이미 삭제 중·지워짐」은 전송에서 뺀다. 미리보기
  404/405·실패면 목록 수로 대신하고 정확한 수를 모르는 배치(5,000개 이상)는 막는다.
- 결과 줄 「배치 N개(작업 M개) 백그라운드 삭제 시작」, 제외 줄은 서버 숫자를 끼운 같은 문구, 정리 줄 「배치 삭제 중 k건」(5초 폴링 —
  끝나면 목록·배치·스케줄·사용량 무효화). 배치 칸 「삭제 중 37%」/「삭제 지연」, 배치 상세 알약 「삭제 중」 + 진행 카드(지연 사유·문제 작업
  링크) + 바꾸는 버튼 숨김, 「배치 삭제」(기록만) 창의 상한 정보 줄(막지 않음), 미등록 스토리지 정리 창의 상한 줄·넘는 배치 경고(정리는
  막지 않음)·백그라운드 몫 결과, `stateHelp` 「Deleting」.

**리뷰 반영**(같은 날 독립 리뷰 — 원문 job 산출물 `cap50k/review_*`):
- 백그라운드 청크가 「끝까지 훑었는데 고를 것이 없음」을 커서부터 훑은 바퀴 끝에서도 막힘으로 판정한다(`delete_batch_chunk`) — 예전엔
  되감기만 해서, 고를 수 없는 자식이 훑기 예산(5000)보다 많으면 「처음부터 → 예산 소진 → 끝까지 → 되감기」가 끝없이 돌고 blocked·백오프·
  `last_error`·경고 이벤트가 없었다(포탈엔 「삭제 지연」 대신 「삭제 중 N%」). 커서가 없을 때 하한(`commit_order > 0`)도 없앴다(commit_order 0
  자식이 늘 빠지던 구멍 — 음수는 고른 뒤 `batch_changed` 로 막힘). 재판정은 지울 수 있다는데 고르기 술어가 못 고르는 자식(술어 어긋남)은
  `_pick_ok` 로 경합과 갈라 막힘(`request_recently_finished`)으로 표면화. 회귀 5건(5001 자식 실 상수 포함, sqlite·PG).
- 미리보기(`delete_preview`)의 목록 요약을 배치마다 따로 센다 — 고른 배치 전부를 한 UNION ALL 문장으로 세면 5만 자식 배치 10개에서 그
  한 문장이 로컬 1.67–1.71초 API RLock 을 쥐었다(테스트베드 환산 ~1.9–2.5초 — readiness 1초를 넘는다). `perf_cap50k.py` 에 라우트 그대로의
  `preview` 단계를 넣었다(아래).
- 정리 아웃박스 `due()` 는 키 열로만 순위를 매기고 LIMIT 한 뒤 전체 열을 조인한다(같은 순서 — BACKLOG 「정리 루프 틱 비용」에 실측).
- 확인 창: 상한을 넘은 배치 줄이 목록의 낡은 하한(「작업 5,001개 이상」) 대신 미리보기가 센 수(「작업 73,210개」, 모르면 「…개 이상」)를
  보이고 첫 줄·버튼도 같은 수다. 같은 화면의 숫자(첫 줄·연산별·버튼·결과 줄·정리 줄·선택 줄의 성공 scan)를 `n0` 천 단위 구분으로 통일.
- 문서: 그물 불릿·`BATCH_DELETE_TABLES` 주석·감사 함수 docstring 의 「두 경로」 → 세 경로(+ 묘비가 스케줄·회차를 지운다), 실측 줄 정정(A단계
  문장 수 16 → 7, 「가장 긴 단위」 → 동기 500 트랜잭션).

**실측**(로컬 PG16 unix 소켓, `tests/perf_cap50k.py` — 세계 단건 1만 + 5500 × 10, 측정 배치 자식 5만·항목 5000 **현실 모양**(요청·잡·plan·
결과·이벤트·전이 8·scan 요약), 리뷰 반영 뒤 재측정 — 결과 원문 job 산출물 `cap50k/c5_perf_50000.pg.jsonl`):
- API: A단계 벽시계 0.344초(문장 7개, 가장 긴 문장 133.2ms — 잡 게이트), 묘비 B단계 트랜잭션 **55.6ms**, 그동안 같은 API 커넥션의
  readyz 흉내 최대 대기 **123.2ms**, RSS +124KiB. 동기 500(현실 모양) 한 트랜잭션 0.199초.
- 확인 창 미리보기(라우트 함수 그대로 — 자식 5만 배치 10개를 더 만든 요청 56.5만 세계): 10개 선택이면 문장 112개·가장 긴 문장
  **155.9–187.4ms**·같은 커넥션 readyz 흉내 최대 145.9–177.8ms·벽시계 5.1–6.2초(문장 사이에 RLock 이 풀린다), 2개 143–150ms, 1개 146–153ms.
- 컨트롤러: 5만을 **12.2초**에 다 지움(7틱 · 트랜잭션 502 — 청크 최대 65.6ms · 중앙 19.0ms), 그동안 **API 커넥션** readyz 흉내 최대 23.8ms
  (p99 1.2ms — 같은 프로세스의 GIL 몫, 별 프로세스면 더 작다), RSS +2.9MiB.
- 감사: start 1 + chunk 500(12.8MB) + delete 1(32KB). 아웃박스 5만 행 전부 `purge_group = batch_id`, 그 대기열(50,500행)에서 `due(limit 20)`
  104–106ms(컨트롤러 커넥션).
- 판정 기준 (2) 충족: API 가 RLock 을 쥐는 가장 긴 단위는 동기 500 한 트랜잭션 0.199초(로컬 0.20–0.22초 · 테스트베드 환산 0.36–0.43초),
  그다음이 미리보기 문장 ≤ 187ms·A단계 문장 ≤ 133ms — 모두 1초 프로브 타임아웃 미만이고, 한 트랜잭션 방식의 20.9–22.9초 대비 ~100배 짧다.

**검증**: 백엔드 관련 파일 sqlite + 로컬 PG16(새 `tests/test_batch_delete_background.py` 33 — PG 동시 묘비 1승 포함; PG 의 기존 batch·purge
계열 18파일 668 passed — 실패 7건은 sqlite 전용 SQL(AUTOINCREMENT) 마이그레이션 테스트로 HEAD 와 같다), vitest 전체 녹색, `tsc -b` 0,
빌드 외부 URL 0, e2e 39 passed(06 에 「E7 상한 표시」 추가 — 414·768·1280 툴바 높이 불변·창 문제 상자·백그라운드 결과·서버 거절 문구).
리뷰 반영 뒤 최종 게이트(순차): 백엔드 전체 `-x` **6062 passed · 19 skipped**(첫 회는 기존 시간 경계 테스트 `test_api_metrics` 0.505초 > 0.5초 1건
— 단독 재실행 통과, 전체 재실행 녹색), 로컬 PG16 관련 9파일 **403 passed**(새 회귀 6건 포함 — `test_batch_delete_background.py` 39), vitest
**2136 passed**(124 파일), `tsc -b` 0, 빌드 외부 URL 0, e2e **39 passed**. 리뷰 프로브(`cap50k/review_c5_probe_rewind.py` — 「5001 자식이면 끝내
blocked 가 없다」)는 이제 둘째 호출마다 `request_job_active` 로 막혀 그 단언이 깨진다(`cap50k/c5_probe_rewind_after_fix.txt`).

**커밋 보안 리뷰 반영(cc52a7e3)**: 미리보기가 상한을 넘는 배치의 정확한 수를 내려고 `10·cap + 1`(=500,001) 까지 한 문장으로 다시 셌다 — 자식이
아주 많은 배치에서 dms-api 의 DB RLock 을 수 초 쥘 수 있어, 재계산을 지우고 동기·백그라운드 판정과 같은 하한(`count_at_least = cap + 1`)만 싣는다(포탈은
「N개 이상」으로 그린다). d34 는 이 수정 전 빌드라 릴리스하지 않고 건너뛰었다.

**라이브 실증(d35, 2026-11-19 — 커밋 cc52a7e3, 빌드 210초, 릴리스 4종 Applied, 오버레이 c2eeab6c, 설치기 `--only S11` 로 CM 두 키 `50000`·`500` + api·
controller·agent 재시작)** — 실 Chrome(테스트베드 CA 신뢰), 20화면 콘솔 오류·실패 요청·가로 넘침 0:
① `GET /api/admin/request-purges`·미리보기의 limits = 상한 50000·동기 500, 기존 배치 e5fe(자식 10) 미리보기 `sync`. ② 툴바 상한 줄 「…배치당 작업 50,000개·
항목 10,000개 · 합계 작업 100,000개」(1280·390), 확인 창 칩 「바로 삭제」. ③ CM 에 **잠시** `MAX=5` → 툴바가 role=alert 「삭제 불가 — 작업이 한 번에 지울 수
있는 상한 5개를 넘습니다(6개 이상)」, 확인 창 제출은 동의 뒤에도 잠김, API 직접 호출은 `batch_delete_too_large`(limit 5) 거절·지워진 것 0. ④ CM 에 **잠시**
`SYNC=0` → 새 소형 scan 배치를 포탈에서 지우자 117ms 에 「배치 1개(작업 3개) 백그라운드 삭제 시작」·「배치 삭제 중 1건」·배치 칸 「삭제 중 0%」, 그동안 상태
`Deleting`·`:rescan`·PATCH 409 `batch_deleting`·미리보기 `deleting`·다시 지우면 `already:true`, 묘비 → 배치 사라짐 1초 미만(다른 배치로 한 번 더 ≈4초 = 컨트롤러
한 주기), 감사 `delete_start` → `delete_chunk` → `delete`, 아웃박스 `purge_group = batch_id`(읽기 전용 조회), 아티팩트 실제 삭제. ⑤ 기본값으로 되돌린 뒤
새 소형 배치 → 68ms 에 「배치 1개(작업 2개) 삭제됨」(동기 — 「삭제 중」 없음, 감사 작업별 2 + 배치 1, `purge_group` NULL). 끝에 설치기 S11 로 원복
(CM `50000 500`, 파드 Ready, `/readyz` 200). 실증이 만든 배치 5개는 모두 지웠고 e5fe 는 그대로.
- 실증 뒤 다듬기(d36): 확인 창 「연산별」 줄이 상한 초과 배치의 하한 수에 「이상」을 빠뜨렸다(첫 줄·버튼은 「6개 이상」인데 「scan 6」) →
  `opCountLine` 이 하한 행을 받아 「scan 6 이상」(vitest 1건). 이미 열려 있던 **끝난 배치 상세 탭**은 주기 폴링을 멈춰(탭 복귀·재연결 때만 다시 읽는다)
  계속 보는 동안 「삭제 중」이 뜨지 않는 기존 동작 — 그 탭의 버튼은 서버가 409/404 로 막는다(BACKLOG).
- d36 배포 — 포탈 빌드 d36(소스 06427f15, 3종, 210초) → 릴리스 dms-agent → dms-api → dms-controller → job-image 모두 Applied(live == 동봉 d36) →
  오버레이 d35 → d36(293f4043). 설치기 S11 은 돌리지 않았다 — 그래서 라이브 ConfigMap `DMS_JOB_IMAGE` 는 d35 로 남아 있고, 실제로 쓰는 잡 이미지는 DB
  `control_state.job_image`(릴리스 d36 — `job_image.resolve_job_image` 는 DB 가 env 를 이긴다)다. 다음 `--only S11` 이 CM 을 d36 으로 맞춘다(guard-images.sh 는
  `DMS_JOB_IMAGE` 를 대조하지 않아 S11 이 멈추지 않는다).

### ✅ 테스트베드 사내 AD SSO 환경 구축·실증 + 실증 결함 3건 수정 — **완료**(2026-11-18, d32 → d33)

사용자 요청(2026-11-18): 「테스트베드에 ad sso 환경을 구축하고 실증까지 완료해줘」(답변·문서는 한국어로).

- **환경**(testbed IaC — 커밋은 사용자, `make idp-up`·`idp-config`·`idp-check`·`idp-dms-secret`·`idp-down`, testbed `docs/ARCHITECTURE.md` §24, 이
  저장소 deploy/testbed/README §6-7): 호스트 docker 에 Samba AD DC 4.22(`dms-idp-dc` — docker 내부망만) + PostgreSQL 17 + Keycloak 26.8.1
  (`dms-idp-kc`, `https://idp.dms-k8s.local:8444/realms/dms-testbed`, `10.10.10.1:8444` 에만, 인증서 = 테스트베드 CA). Keycloak 이 AD 를 LDAPS·
  READ_ONLY 로 연동(아이디 = sAMAccountName, `mail` → email, Trust Email 끔), client `dms-k8s`(confidential·Standard flow·PKCE S256 필수·redirect
  `https://dms-k8s.local/api/auth/oidc/callback`). 이름 `idp.dms-k8s.local → 10.10.10.1` 은 호스트 `/etc/hosts` 와 dmsk8s CoreDNS hosts 블록(IaC 확장).
  비밀(client secret·AD/Keycloak 관리자·시험 계정 비밀번호)은 testbed `files/idp/`(0600, git 제외)에만 — 이 저장소에는 없다. IdP 인수 검사 59/0,
  `validate.sh dmsk8s` 268/0.
- **DMS 배포**: 사내 SSO 코드(55cf39cc)를 d32 로 빌드·릴리스(오버레이 bad91f2a), dms-api 의 `envFrom` 선택 Secret 은 설치기 `--only S11` 로 적용
  (포탈 릴리스는 이미지만 바꾼다), Secret `dms/dms-oidc`(`make idp-dms-secret`) 뒤 `rollout restart deploy/dms-api` → `sso-info` enabled.
- **실 Chrome 실증**(Chrome 147, 테스트베드 CA 신뢰 — TLS 검증 켬, 시나리오마다 새 컨텍스트, 1280·390): 로그인 화면 SSO 버튼 · 새 AD 계정 `sso.alice`
  → Keycloak 화면 → 포탈(계정 user 로 자동 생성, email sso.alice@example.com, 감사 actor `sso`) · 딥링크 복귀(`/jobs/new`) · 기존 특권 계정 `mason`
  → role admin 그대로 · `admin` → `sso_username_invalid` · `@other.example` → `sso_email_domain_mismatch`(계정 안 생김) · DMS 에서 비활성화한
  `sso.disabled` → 기존 세션도 401, 다시 SSO → `account_disabled` · 로그아웃 → 401, 다시 SSO 는 IdP 세션으로 비밀번호 없이 1초 · email_verified:
  `DMS_OIDC_REQUIRE_EMAIL_VERIFIED=true` → `sso_email_unverified`, Keycloak Trust Email 켬 → 통과(id_token `email_verified: true`), 기본값으로 복원 뒤
  다시 통과 · 쿠키 `dms_session` Secure·HttpOnly·SameSite=Lax · 콜백 재전송 → `sso_state_invalid` · 49화면 예상 밖 콘솔 오류·실패 요청·가로 넘침 0 ·
  사용자가 Keycloak account API 로 자기 email 을 바꾸려 하면 400(READ_ONLY). 로그에 client secret·비밀번호 없음.
- **실증에서 찾아 고친 결함(d33)**:
  1. 로그아웃 직후 로그인 화면에 SSO 버튼이 없었다(3/3 재현, 새로고침해야 보임) — 로그아웃은 nav 를 먼저 하고(2026-10-16 me 재조회 루프 수정) 훅의
     `qc.clear()` 가 로그인 화면이 막 시작한 `sso-info` 조회를 지웠다. SSO 로 만든 계정은 비밀번호가 없어 다시 들어올 길이 없어지는 결함. 로그아웃
     정리를 「공개 조회(`sso-info`·`mail-info`·`portal-info`)는 남기고 나머지만 지움」으로(`useAuth.PUBLIC_QUERY_KEYS`) — 테스트 2개(옛 코드 빨간불 확인).
  2. dms 로거의 INFO 가 출력되지 않아(dms 에 로깅 설정이 없고 uvicorn 은 자기 로거만 설정) 런북 §6-9 가 판단 근거로 든 `sso: <아이디> email_verified=…`
     줄이 0건이었다 → 그 줄을 WARNING 으로.
  3. uvicorn 접근 로그가 콜백 URL 을 쿼리째(code·state·session_state·iss) 남겼다 → `uvicorn.access` 로거 필터(`routes_oidc.RedactCallbackQuery`,
     create_app 이 한 번만 붙임)가 `/api/auth/oidc/callback?<redacted>` 로 바꾼다.
  - 문서 정정: §6-9 의 client secret 확인법 — Keycloak 26 은 비밀이 맞아도 틀려도 401 `unauthorized_client` 라 `error_description` 으로 가른다(테스트베드
    실측), 확인 명령은 비밀을 argv 대신 파일(curl `-K`)로.
  - **d33 재실증**(커밋 124d51a0, 빌드 225초, 릴리스 4종 Applied, 오버레이 d32 → d33): 로그아웃(SPA 이동) 직후 `/login` 에 SSO 버튼 보임 — 요청 순서는
    그대로(logout 시작 → sso-info 시작 → logout 끝)인데 버튼이 남는다, 다시 SSO 는 IdP 세션으로 1초. dms-api 로그 `sso: sso.alice email_verified=False …`
    4줄(WARNING). 콜백 접근 로그 6줄 모두 `/api/auth/oidc/callback?<redacted>`, `code=`·`state=` 0건. 새 로그인·쿠키 속성·콜백 재전송 거절도 다시 통과.
- **알아둘 점**: SSO 는 호스트 브라우저에서만(IdP·redirect 가 호스트 경로 — LAN·Tailscale 기기는 비밀번호 로그인). 로그인 실패 뒤 다시 누르면 IdP 세션이
  남아 같은 AD 계정으로 같은 오류가 난다(다른 계정으로 바꾸려면 IdP 로그아웃 — `end_session` 은 남은 일). 새 SSO 사용자는 RoleBinding 맵핑 전에는
  PV 를 고를 수 없다(설계대로). 테스트베드 CA 에 keyUsage 가 없어 Python 3.13 의 엄격 검증이면 거부된다(지금 이미지 3.11 — 판 올릴 때 CA 재발급 필요).
- **남긴 것**: DMS 계정 `sso.alice`(user)·`sso.disabled`(user, 비활성), Secret `dms-oidc`(키 6개 — 기본값), Keycloak Trust Email 끔.

### ✅ 사내 AD SSO 로그인(OIDC Authorization Code + PKCE) — **구현 완료**(2026-11-18, 프로덕션 사이트에서 먼저 구현·실 AD 로그인 검증 → 이 브랜치 반영)

사용자 요청(2026-11-18): 「프로덕션 클러스터에서 AD SSO 인증 방식이 필요해서 구현 후 검증까지 했어. 첨부한 내용 토대로 구현해줘. 보안 및
비밀번호가 깃허브에 올라가지 않도록 주의하고, 설치 문서도 업데이트해」 — 사이트(사내망, 원격 push 불가)에서 만든 인계 문서(base 2365ae61, 14파일
패치·설계 결정·검증 결과)를 이 브랜치에 그대로 반영했다.

- **동작**: 로그인 화면에 아이디·비밀번호와 함께 SSO 버튼(글자 = `DMS_OIDC_BUTTON_LABEL`). 인증은 IdP(Keycloak — 사내 AD 대행)가 하고 DMS 는 OIDC 만
  말한다(AD·LDAP 에 붙지 않음 — dms-k8s 의 LDAP 코드 삭제 원칙 그대로). 이메일 `<아이디>@<DMS_ACCOUNT_EMAIL_DOMAIN>` 의 `<아이디>`(소문자)가 계정
  아이디(다른 도메인 거절). 계정이 없으면 user 로 만들고(`DMS_OIDC_AUTO_PROVISION`, 기본 true — 비밀번호는 아무도 모르는 무작위 값), 특권 목록 이름은
  만들지 않으며(`sso_privileged_account_missing` — 이미 있는 특권 계정은 그 역할로 로그인), root·admin·예약어는 막고(`sso_username_invalid`), 비활성은
  `account_disabled`. 세션은 비밀번호 로그인과 같은 username + epoch(비밀번호 변경·재생성이면 SSO 세션도 `session_revoked`). 로그아웃은 DMS 세션만.
- **프로토콜**(`src/dms/api/oidc.py`, 표준 라이브러리만): Authorization Code + PKCE S256 + state + nonce, confidential client(client_secret_basic),
  discovery(issuer 일치·https 끝점·1시간 캐시), id_token 은 클레임(iss·aud·azp·exp·iat·nonce) 대조 — 토큰 끝점에서 인증서를 검증한 TLS 로 직접 받은
  id_token 은 OIDC Core 3.1.3.7(6)이 서명 대신 TLS 검증을 허용하므로 **TLS 검증을 끄는 옵션은 없다**(사내 CA = `DMS_OIDC_CA_PEM`). 프록시 env 무시
  (`ProxyHandler({})`). interaction(state·nonce·verifier·시각·next)은 서명된 세션 쿠키에 얼리고 콜백에서 pop(재전송 차단, 10분 TTL).
- **라우트**(`src/dms/api/routes_oidc.py`): GET `/api/auth/sso-info`·`/api/auth/oidc/login?next=`(302)·`/api/auth/oidc/callback`(303). 실패는 모두
  `/login?sso_error=<사유>` → 로그인 화면이 사유 문구로 보이고 주소창에서 지운다. `next` 는 같은 출처 포탈 경로만(`//`·`\`·`/api/`·`/login`·제어문자·
  512자 초과 → `/`).
- **설정**: 선택 Secret `dms-oidc` 를 dms-api 컨테이너만 `envFrom`(optional)으로 읽는다(`deploy/k8s/40-api.yaml` — migrate init 에는 안 붙음). 넷
  (ISSUER·CLIENT_ID·CLIENT_SECRET·REDIRECT_URI)이 다 있어야 켜지고 일부만·http·PEM 아님·자리표시자면 기동 거부(`config._oidc_values`). Secret 이
  없는 사이트는 지금과 같다(버튼 없음). 값 변경 뒤엔 `rollout restart deploy/dms-api`(S11 의 설정 재시작은 dms-config·dms-secrets 만 본다).
- **사유 코드 12개** 양쪽 등록(`reasonCodes.json` + `api.ts`): sso_not_configured·sso_denied·sso_state_invalid·sso_upstream_unreachable·
  sso_upstream_error·sso_token_invalid·sso_email_missing·sso_email_domain_mismatch·sso_email_unverified·sso_username_invalid·
  sso_account_not_found·sso_privileged_account_missing(`account_disabled` 는 기존). 추출기 `_EXCEPTION_CTORS` 에 `OidcError`.
- **반영 때 더한 것 — email_verified**(보안 리뷰: IdP 가 사용자에게 email 수정을 허용하면 남의 메일로 그 계정에 들어온다): 선택 키
  `DMS_OIDC_REQUIRE_EMAIL_VERIFIED`(켜면 IdP 가 `email_verified: true` 로 표시한 메일만 받는다 — 아니면 계정을 만들기 전에 `sso_email_unverified`).
  email_verified 는 email 과 같은 출처(id_token 또는 userinfo)에서 읽는다. **기본은 끔** — Keycloak 의 AD(LDAP) 연동은 「Trust Email」 없이는 AD
  메일도 false 라 켜 두면 검증된 사이트 SSO 가 막힐 수 있다. 끈 상태에서는 확인되지 않은 메일 로그인을 dms-api 로그(`sso: <아이디> email_verified=…`)
  에 남겨 운영자가 IdP 표시를 보고 켜게 하고, 결속은 「사용자가 IdP 에서 자기 email 을 못 바꾼다」는 IdP 설정이 지킨다(런북 §6-9). IdP `sub` 를
  계정에 묶는 안은 DB 칸이 필요하고 첫 연결을 먼저 한 쪽을 막지 못해 하지 않았다(남은 것).
- **비밀이 저장소에 들어가지 않게**: 패치에 client secret 은 없다(사이트 Secret 에만). 이 브랜치 반영 때 런북의 사이트 실제 값(사내 IdP 주소·client
  이름·포탈 FQDN) 예시는 일반 자리표시자로 바꿨다(런북은 사이트 중립 — 저장소에 사내 호스트 이름이 없던 상태 유지). `.gitignore` 에 `*.secret`·
  `.secret`·`*.secret.txt` 를 더했다(비밀 파일은 저장소 밖이 원칙 — 그물). 런북에 비밀 파일 위치(저장소 밖 0700)·`--from-file` 만·셸 기록 금지를 적었다.
- **문서**: deploy/README §6 머리(6-9 는 선택)·§6-8 확인표 한 줄·새 §6-9(규칙 표, IdP 쪽 준비 — **사용자가 IdP 에서 자기 email 을 바꿀 수 없어야
  한다**(바꿀 수 있으면 남의 메일로 그 사람 계정에 들어온다 — 인계 패치에 없던 주의, 이 반영에서 더함), Secret 만들기·회전·끄기, 확인·문제 해결),
  prod README 설치 순서 6) 의 선택 줄·운영 「시크릿」 줄, ARCHITECTURE 모듈 표 2행·인증 불변식 한 줄.
- **검증**(이 브랜치): `tests/test_oidc_sso.py` 38 passed, Login.test 31 passed(새 3개), `tsc -b`, 백엔드·프런트 전체·e2e(아래 커밋 메시지). 사이트 쪽
  검증(인계 문서): 이 패치로 수동 빌드한 시험 이미지로 실 IdP·실 AD 로그인 성공(2026-11-18).
- **남은 것(선택)**: 로그아웃 때 IdP `end_session_endpoint`, SSO 로그인 성공·실패를 보안 이벤트로, 계정 화면에 「SSO 로 만든 계정」 표시. 사이트는
  이 브랜치를 pull 한 뒤 포탈에서 정식 태그로 빌드·릴리스하면 된다(Secret `dms-oidc` 는 그대로).

### 🔧 pull-probe 노드 여러 대 지정 수정 — 마스터 3대 사전 pull 에서 apiserver 가 DaemonSet 생성을 거부하던 것 — **수정 완료**(2026-11-17, 이미지 변경 없음)

- **증상**(2026-11-17 프로덕션, K8s 1.35, 마스터 3대): 설치기 S11 끝의 마스터 `dms` 이미지 사전 pull(`pull-probe.py --nodes <마스터 쉼표 목록>`)이
  임시 DaemonSet 을 만들지 못했다 — apiserver 거부 문구 「must be only one value when `operator` is 'In'」. S11 은 이 실패를 WARN(「마스터 사전 pull
  실패 — 앱은 떴다」)으로만 다루므로 설치는 끝났지만, 장애조치 때 새 마스터에 `dms` 이미지가 캐시돼 있다는 전제(설계 6-3)가 깨져 있었다.
- **원인**: `deploy/pull-probe.py` `ds_manifest` 가 `--nodes` 의 노드 전부를 nodeAffinity 의 **한 term** 에 `matchFields: [{key: metadata.name,
  operator: In, values: [노드 여러 개]}]` 로 넣었다(2026-11-03 ⑦ 설치기 e8dcb28e 부터). K8s 는 노드 **필드** 셀렉터(`matchFields` `metadata.name`)의
  `In`·`NotIn` 값을 하나로 제한한다(라벨 셀렉터 `matchExpressions` 와 다르다). 테스트베드 dmsk8s 는 control-plane 이 1대라 값이 늘 하나여서 드러나지
  않았고, 기존 테스트(`test_main_nodes_option_pins_the_daemonset_and_judges_only_those`)도 노드 하나만 줬다.
- **수정**: 노드마다 term 하나(`nodeSelectorTerms` 끼리는 OR) — `[{matchFields: [{metadata.name In [cp1]}]}, {matchFields: [{metadata.name In [cp2]}]}, …]`.
  `kubernetes.io/hostname` 라벨(`matchExpressions` — 값 여러 개 허용)은 노드 이름과 다를 수 있어 쓰지 않았다. S6 pull 프로브(`--nodes` 없이 모든
  노드)는 affinity 를 싣지 않으므로 영향이 없다.
- **테스트**: `tests/test_pull_probe.py` `test_main_several_nodes_get_one_selector_term_each`(노드 두 대 → term 두 개, 어느 `In` 도 값이 하나, 두 노드 모두
  판정).
- **적용**: 이미지 변경 없음 — 설치기 도우미라 다음 S11 실행부터. 사이트는 이 커밋으로 체크아웃을 올린 뒤 `--only S11` 을 다시 돌리면 사전 pull 이 다시 돈다.
- **검증**: `test_pull_probe` 를 바로 아래 항목 「검증」의 22파일 실행에 넣어 돌렸다(884 passed·2 skipped). 테스트베드는 control-plane 이 1대라 노드 여러 대
  지정은 라이브로 실증하지 못했다 — 다음 프로덕션 S11 의 「마스터 사전 pull」 줄이 OK 인지 본다.

### 🔧 설치기 S11 에이전트 DaemonSet 대기를 노드 수 비례로 — 6노드 사이트의 정상 롤링(약 600초)을 고정 180초가 끊던 것 + 시간 초과 뒤 재실행이 설정 재시작을 되풀이하던 것 — **수정 완료·실증**(2026-11-17, 이미지 변경 없음)

- **사용자 사례**(2026-11-17): 「Daemon rollout 기본 대기시간이 180초로 되있어? 실제로는 더 오래걸리는거 같은데 6개 노드에서 600초가
  걸렸어. Daemonset 대기 시간을 노드수에 비례하게 할수있어?」 — 6노드 사이트에서 ds/dms-agent 롤링이 약 600초(노드당 ~100초) 걸렸다.
- **원인**: 설치기 S11 은 dms-api·dms-controller·ds/dms-agent 를 모두 `kubectl rollout status --timeout=${ROLLOUT_TIMEOUT:-180}s` 로 기다렸다(본 대기와
  설정 재시작 뒤 대기 두 곳 — 2026-11-03 단계 설치기부터 고정값). 에이전트 DS 는 `deploy/k8s/50-agent-daemonset.yaml` 에 updateStrategy 가 없어
  apiserver 기본값(RollingUpdate maxUnavailable 1·maxSurge 0)으로 노드를 하나씩 바꾸고, `--timeout` 은 진행과 무관한 총 시간이다. 그래서 노드 수 ×
  노드당 시간이 180초(+ 앞의 migrate·api·controller 대기 — DS 는 apply 순간부터 돈다)를 넘으면 정상 롤아웃도 `ds/dms-agent rollout 시간 초과` 로
  멈췄다. 설치기에서 노드 수를 따지지 않고 DaemonSet 을 기다리는 곳은 이 두 대기뿐이다(S2b·S6·S10 의 일회용 프로브 DS 는 모든 노드에 한꺼번에
  만들어 폴링하므로 노드 수 비례가 아니다). 노드당 시간은 테스트베드 라이브 실측(revision 38)이 약 31초이고 그중 30초가 옛 파드 종료 유예다(에이전트
  PID 1 에 SIGTERM 처리기가 없어 terminationGracePeriodSeconds 30초를 채운다). 사이트의 ~100초는 여기에 새 태그 pull(태그마다 바뀌는 앱 레이어 약
  73 MiB)·기동이 더해진 것으로 보인다(추정 — 미검증).
- **포탈 릴리스는 바꾸지 않았다**: `rollout_watcher._note_daemonset_progress` 가 DaemonSet 의 `updatedNumberScheduled` 가 늘 때마다 시계를 다시
  걸어(「마지막 진행 뒤 `DMS_ROLLOUT_TIMEOUT_SECONDS`=600초」) 이미 노드 수와 무관하게 정상 롤링을 통과시킨다. 조사 때 실제 RolloutWatcher·
  ReleasesRepository 에 가짜 러너로 6노드 × 노드당 100초를 돌려 600초에 Applied, 노드 한 주기 610초에서만 `rollout_timeout` 임을 확인했다(기존
  `test_daemonset_progress_pushes_back_the_wallclock` 이 같은 규칙을 고정). 노드 수 × 상한 같은 절대 상한은 오히려 한 노드 정체(ImagePullBackOff)를
  늦게 잡고 새 컬럼이 필요하다. 그래서 포탈 쪽은 설명만 고쳤다(`deploy/README.md` §9-7·`20-config.yaml` 주석 — 「벽시계 상한」 → 「마지막 진행 뒤의
  정체 상한」, 값은 그대로).
- **수정**(`deploy/install.sh` `wait_rollout`·`ds_wait_plan` — 두 대기 자리의 공용 도우미): Deployment 는 `ROLLOUT_TIMEOUT`(180) 그대로이고,
  DaemonSet 대기 상한 = **max(`ROLLOUT_TIMEOUT`, ceil(노드 수 ÷ 동시 교체 수) × `ROLLOUT_TIMEOUT_PER_NODE`)**. 노드 수 = `.status.desiredNumberScheduled`
  (taint·셀렉터를 반영한, DS 가 실제로 도는 노드 수 — `get nodes` 는 NoSchedule 마스터까지 센다). 동시 교체 수 = `.spec.updateStrategy.rollingUpdate.
  maxUnavailable`: 정수는 그대로, 퍼센트는 노드 수 기준 올림(DS 컨트롤러와 같다), 최소 1. maxSurge 는 세지 않는다 — 켜면 더 빨리 끝날 뿐이라
  더 기다리는 쪽(안전)이다. 못 읽은 값은 모름(≠ 0): 노드 수 모름 → 하한, 동시 교체 수 모름 → 1. 노드 0대는 정상값(→ 하한), `ROLLOUT_TIMEOUT=0`
  (kubectl: 끝날 때까지)은 그대로 존중한다. 이 값은 상한(kubectl `--timeout`)이라 롤링이 끝나면 바로 넘어간다. 기다리기 전에
  `[INFO] ds/dms-agent: 최대 900초 기다린다(노드 6대 ÷ 동시 1 → 6차례 × 150초)` 를 보이고(하한이 이기면 `최대 180초 기다린다(노드 1대 ÷ 동시 1 →
  1차례 × 150초 = 150초 < 하한 ROLLOUT_TIMEOUT)`), 시간 초과 줄 `ds/dms-agent rollout 시간 초과(대기 상한 900초: <계산> — kubectl … describe
  ds/dms-agent; 롤링은 클러스터에서 계속된다)` 는 더 기다리게 다시 돌 명령(노드 비례가 이겼으면 `ROLLOUT_TIMEOUT_PER_NODE=<두 배> … --only S11`,
  하한이 이겼거나 노드 수를 몰랐으면 `ROLLOUT_TIMEOUT=<두 배> …`)을 싣는다. `ROLLOUT_TIMEOUT=0` 인데 실패하면 시간 초과가 아니므로(apiserver 연결·
  권한 등) `rollout 대기 실패(ROLLOUT_TIMEOUT=0 — 시간 제한 없이 기다렸는데 실패: …)` 로 멈추고 다시 돌 명령을 싣지 않는다(Deployment 대기도 같다).
  두 손잡이는 셸 산술에 들어가므로 정수가 아니거나 앞자리 0(8진수)이면 클러스터에 붙기 전에 exit 2.
- **재실행이 설정 재시작을 되풀이하던 것**(같은 날 리뷰 — 심 kubectl 재현): 설정 표지(`dms.io/install-config-rev`)를 모든 대기가 끝난 뒤(S11 끝
  annotate 루프)에만 썼다. 그래서 대기에서 시간 초과로 멈춘 실행은 옛 rev 를 남겼고, 시간 초과 줄의 안내대로 다시 돈 `--only S11` 은 apply 가 바꾸는
  것이 없어 지문이 같으니(`rollout restart` 의 `restartedAt` 도 client-side apply 가 지우지 않는다) 표지 ≠ 현재 rev 로 셋 다 한 번 더 재시작했다 —
  6노드면 에이전트 롤링을 통째로 한 번 더 하고 에이전트 대기도 두 번(노드당 300초면 1800 + 1800초). 본 대기 시간 초과·재시작 대기 시간 초과 둘 다
  같았다. 고친 모양(`stage_S11` 「왜」 주석): 재시작 판정을 apply 직후로 옮기고, 표지는 재시작이 필요 없다고 확정되는 즉시(처음 만듦·새 파드
  템플릿 = 새 파드가 새 설정으로 뜬다, 설정 그대로 — 대기 전), 재시작할 워크로드는 `rollout restart` 가 성공한 직후(대기 전) 쓴다(`mark_cfg`).
  재시작 전에 미리 쓰지는 않는다 — 그 사이에 멈추면 다음 실행이 필요한 재시작을 놓친다. 표지가 이미 현재 rev 면 다시 쓰지 않는다.
- **기본값·손잡이**: 새 env `ROLLOUT_TIMEOUT_PER_NODE`(기본 **150초** — 사용자 사례 노드당 ~100초에 50% 여유. 6노드 900초, 테스트베드 2노드 300초,
  1노드는 하한 180초). 기존 `ROLLOUT_TIMEOUT`(180)은 S4 레지스트리·S9b Volcano·S11 api·controller 대기와 DaemonSet 하한으로 그대로다(기본을 올리면
  그 대기까지 늘어나므로 손대지 않았다). 지금 사이트에서의 우회(이 커밋 전): `ROLLOUT_TIMEOUT=900 sh deploy/install.sh --context <ctx> --site <site>
  --only S11`.
- **테스트**: 심 kubectl(`tests/fixtures/install_stages/fake_kubectl.py`)이 `get ds/<이름> -o jsonpath={.status.desiredNumberScheduled}`·
  `{.spec.updateStrategy.rollingUpdate.maxUnavailable}` 에 답한다(세계 `ds_status` — 기본은 라이브 테스트베드 모양 2노드·1, 빈 문자열 = 모름, 없는
  DS 는 NotFound — 라이브 dmsk8s 에서 같은 jsonpath 가 `2`·`1`·NotFound rc 1 임을 읽기로 확인). 새 테스트(`tests/test_install_stages.py`):
  `test_s11_daemonset_wait_scales_with_node_count`(2·6·1노드, 25%·10%, maxUnavailable 0·빈 값·`08%`, 노드 수 빈 값·0, 손잡이 200·하한 1000·하한 0 —
  13경우, Deployment 대기는 늘 `ROLLOUT_TIMEOUT` 인지와 [INFO] 줄까지)·`test_s11_daemonset_node_count_read_error_falls_back_to_the_floor`(kubectl 오류도
  모름)·`test_s11_config_restart_waits_for_the_daemonset_by_node_count_too`(재시작 뒤 대기도 900초)·`test_s11_daemonset_timeout_says_how_to_wait_longer`
  (세 경우의 안내 명령·「대기 상한」)·`test_s11_no_time_limit_failure_is_not_called_a_timeout`(`ROLLOUT_TIMEOUT=0` + rollout status 실패 — DS·Deployment,
  「시간 초과」·「더 기다리게 다시」 없음)·`test_rollout_timeout_knobs_must_be_integers_before_touching_the_cluster`(클러스터 호출 0), 멈춤 표 STOPS 에
  `S11-agent-rollout`, `--help` 에 새 손잡이. 재실행 반복: `test_s11_rerun_after_rollout_timeout_does_not_restart_again`(1회차 = 6노드에서 본 대기
  시간 초과(새 템플릿 + 설정 변경) 또는 재시작 대기 시간 초과(템플릿 그대로 + 설정 변경 — 심 kubectl 세계 `fail_rollout_after_restart` 로 재시작 뒤
  대기만 실패) → 표지가 이미 새 rev, 2회차 = 시간 초과 줄의 명령(`ROLLOUT_TIMEOUT_PER_NODE=300 … --only S11`) 그대로 → 재시작 없음·에이전트 대기 1800초
  한 번)·`test_s11_config_marker_waits_for_a_successful_restart`(재시작 전에 멈추면 표지는 옛 rev 그대로 → 다음 실행이 재시작 — 표지를 apply 직후
  일괄로 쓰는 「고침」을 막는 그물). 옛 install.sh 로 돌리면 노드 비례 쪽 새 테스트 24건이 빨간불이었고, 리뷰 반영 전 install.sh 로는 재실행 테스트
  2건이 1회차 표지(cm=5 그대로)에서, 표지 단언을 빼면 2회차 재시작 셋·1800초 두 번에서 빨간불임을 확인했다. 돌린 것은 아래 「검증」.
- **문서**: `deploy/README.md` §2h(env 목록에 `ROLLOUT_TIMEOUT`·`ROLLOUT_TIMEOUT_PER_NODE`, S11 행의 멈춤 칸에 rollout 시간 초과, 「멱등」 줄에 표지는
  대기 전, 새 줄 「S11 롤아웃 대기」 — 상한·재실행 때 재시작 되풀이 없음·`ROLLOUT_TIMEOUT=0`)·§9-7(DaemonSet 시계는 마지막 진행 뒤부터 — 노드 수와
  무관), prod README 「설치 뒤 운영」의 설정 바꾸기 줄, `install.sh` 머리 주석(`--help`).
- **적용**: 이미지 변경 없음 — 설치기 스크립트라 다음 S11 실행부터 적용된다(`20-config.yaml` 은 주석만 바뀌었다).
- **검증**: `test_install_stages`(178 passed)·`test_pull_probe` + 배포 문서·스크립트 계약과 롤아웃 판정(`test_ingress_contract`·`test_addons_render`·
  `test_install_verify`·`test_install_detect`·`test_platform_registry_contract`·`test_check_scripts_context`·`test_prod_k8s_overlay`·
  `test_guard_images_context`·`test_testbed_k8s_overlay`·`test_build_and_push_script`·`test_release_manifest_contract`·`test_shared_dir_contract`·
  `test_shared_dir_init`·`test_agent_daemonset_contract`·`test_config_batch`·`test_manifest_tags`·`test_pod_gc`·`test_rollout_watcher`·
  `test_rollout_status`·`test_planner_path_conflict`) 22파일 884 passed·2 skipped, `dash -n` 통과. 라이브 dmsk8s 에서 `ds_wait_plan` 만 떼어 읽기
  전용으로 돌려 상한 300초(`노드 2대 ÷ 동시 1 → 2차례 × 150초`, 안내 `ROLLOUT_TIMEOUT_PER_NODE=300`), `ROLLOUT_TIMEOUT=0` 이면 0(제한 없음)·안내 없음,
  없는 DS 는 모름 → 180초(안내 `ROLLOUT_TIMEOUT=360`)를 확인했다.
- **테스트베드 실증**(2026-11-17 11:20–11:23 KST, 커밋 6b99a1b4 의 설치기, 프로덕션 결함 1 재현): dms-config 의 `DMS_BATCH_SCHEDULE_KEEP_ROUNDS` 를
  `"9"` 로 바꿔(설정 변경 흉내) `ROLLOUT_TIMEOUT=1 ROLLOUT_TIMEOUT_PER_NODE=1 … --only S11` → api·controller·agent `rollout restart` 직후 세 표지가 새
  rev(`cm=8021893`)로 바뀐 뒤 「[FAIL] deploy/dms-api 재시작 rollout 시간 초과(대기 상한 ROLLOUT_TIMEOUT 1초 …)」 `STAGE: S11 FAIL`(DS 줄은 「최대 2초
  기다린다(노드 2대 ÷ 동시 1 → 2차례 × 1초)」). 이어서 기본값으로 같은 명령 → 「설정 변화 없음(또는 이미 새 파드) — 재시작하지 않는다」·「ds/dms-agent: 최대
  300초 기다린다(노드 2대 ÷ 동시 1 → 2차례 × 150초)」·`STAGE: S11 PASS` — 파드 교체는 1회차 한 번뿐(api·controller 02:21:21Z, agent 02:21:51Z·
  02:22:23Z), CM·새 파드 env `10`. 옛 설치기라면 2회차가 셋을 다시 재시작했다(표지가 옛 rev 로 남으므로 — 단위 테스트로 빨간불 재현).

### 🔧 설치기 S11 설정 재시작 누락 수정 — 포탈 릴리스 뒤 태그를 맞추는 S11 이 api·controller 를 다시 띄우지 않던 결함 — **수정 완료**(2026-11-17, 이미지 변경 없음)

바로 아래 d31 배포의 S11 에서 드러난 설치기 결함이다(라이브 증거로 조사한 뒤 고쳤다).

- **라이브 사례**(2026-11-16, 테스트베드): 포탈 릴리스 d31(23:48 KST) 뒤 오버레이 태그를 d31 로 맞춰 돌린
  `sh deploy/install.sh --context dmsk8s --site testbed-k8s --only S11`(23:50 — 라이브 CM `DMS_BATCH_SCHEDULE_KEEP_ROUNDS` `"5"` → `"10"`)이
  `STAGE: S11 PASS` 였는데 「설정이 바뀌어 다시 띄웠다: ds/dms-agent」만 했다. dms-api·dms-controller 는 env 5 그대로 남아
  `kubectl --context dmsk8s -n dms rollout restart deploy/dms-api deploy/dms-controller`(23:52)로 손으로 복구했다. 그때 deploy/dms-api 는
  generation 89 대 RS revision 61(generation 이 파드 교체보다 훨씬 자주 오른다), ds/dms-agent 는 generation 37 = templateGeneration 37 이었다.
- **원인**: `stage_S11` 은 `apply -k` 전후 `metadata.generation` 을 비교해 「바뀜 = 템플릿이 바뀌어 이미 새 파드」로 보고 설정 재시작을
  건너뛰었다(`[ "$_g0" = "$_g1" ] || continue`). 그런데 apiserver 는 Deployment 의 `metadata.annotations` 만 바뀌어도 generation 을 올린다
  (어노테이션이 RS 로 복사되므로 — DaemonSet 은 spec 이 바뀔 때만). 포탈 release(`rollout_runner.image_patch_body` →
  `execution_volcano.patch_workload`, strategic-merge 로 image 만) 뒤 태그를 따라잡는 첫 client-side apply 는 image 는 그대로 두고
  `last-applied-configuration` 어노테이션만 고친다 → api·controller 는 generation 만 오르고 파드는 그대로 → 재시작을 건너뜀. DS 는 generation 이
  그대로라 재시작됐다. 프로덕션도 같은 절차(릴리스 → values 태그 맞춤 → S11)라 같은 구멍이 있었다.
- **수정**(`deploy/install.sh` `stage_S11`·`tmpl_sum`): 「이번 apply 가 파드를 바꿨나」를 `.spec.template` 지문으로 본다 —
  `kubectl get … -o jsonpath={.spec.template}`(키를 정렬한 JSON — 테스트베드에서 두 번 읽어 같은 값임을 확인)을 `cksum` 해 apply 전후로 비교.
  지문이 바뀜 ⇔ 새 ReplicaSet·ControllerRevision ⇔ 파드 교체. 워크로드가 없으면 빈 값 = 이번에 처음 만듦(건너뜀), apply 뒤에 못 읽으면 모름
  (재시작 쪽). 지문 충돌은 재시작이 한 번 더 늘 뿐 빠뜨리지 않는다. 옛 `gen()` 은 S11 만 썼으므로 지웠다. 설치 호스트 전제에 `cksum`(POSIX —
  coreutils·BusyBox): 없으면 앞뒤 지문이 모두 비어 재시작을 조용히 빠뜨리므로 kubectl·python3 처럼 `command -v` 로 확인해 exit 2(클러스터를
  바꾸기 전). 매니페스트는 그대로라 릴리스를 되돌리지 않고 `guard-images.sh` 와도 무관하다.
  - 택하지 않은 안: 파드 템플릿 어노테이션에 설정 해시를 렌더로 찍기 — `dms-secrets` 는 S2 가 만들어 렌더 때 모르고(비밀 원칙), 손 apply 도
    재시작을 일으킨다.
- **테스트**: 심 kubectl(`tests/fixtures/install_stages/fake_kubectl.py`)이 「generation 상승 = 템플릿 변경」이라는 같은 잘못된 가정을 흉내 내고
  있었다. 이제 `get … -o jsonpath={.spec.template}` 에 답하고(세계 `tmpl`), apply 는 `app_bumps` 이거나 처음 만든 워크로드일 때만 템플릿·
  generation 을 함께 바꾸며, 새 손잡이 `app_meta_only` 는 Deployment 의 generation 만 올린다(템플릿·DS 그대로 — 실 Deployment 동작).
  `annotate`(Deployment generation)·`rollout restart`(템플릿 restartedAt)도 실제처럼 바꾼다. 새 테스트
  `test_s11_release_then_config_change_restarts_deployments`(셋 다 재시작 — 옛 install.sh 로 돌리면 라이브와 같은 「다시 띄웠다: ds/dms-agent」로
  빨간불임을 확인)·`test_s11_restart_decision_reads_pod_template_not_generation`(S11 코드가 `metadata.generation` 을 읽지 않고 `.spec.template`
  지문을 쓴다는 계약). 기존 `test_s11_template_change_does_not_double_restart`·`test_s11_restarts_only_when_config_changed` 는 그대로 통과(새 심으로
  옛·새 install.sh 모두). 돌린 것: `test_install_stages` 전체 + 배포 문서·스크립트 계약(`test_ingress_contract`·`test_addons_render`·
  `test_install_verify`·`test_install_detect`·`test_platform_registry_contract`·`test_check_scripts_context`·`test_prod_k8s_overlay`·
  `test_guard_images_context`·`test_testbed_k8s_overlay`·`test_build_and_push_script`·`test_release_manifest_contract`·`test_shared_dir_contract`·
  `test_shared_dir_init`) 14파일 675 passed·2 skipped, `dash -n` 통과, cksum 을 뺀 PATH 에서 exit 2(클러스터 호출 없음) 손 확인.
- **문서**: deploy/README §2h 멱등 줄·§3-4 (b) 3·§3-5(릴리스 뒤 S11 에 설정 변경이 섞여도 셋 다 다시 띄운다), 도구 목록(§0-2·prod README
  「0. 사이트 준비」 6)에 cksum.
- **적용**: 이미지 변경 없음 — 설치기 스크립트라 다음 S11 실행부터 적용된다. 테스트베드 라이브는 위 손 재시작으로 이미 새 설정이다.
- **라이브 실증**(2026-11-17 00:29–00:31 KST, 커밋 72edc604 의 설치기, 테스트베드): ① 변화 없는 S11 → 「설정 변화 없음 — 재시작하지 않는다」, 파드
  시작 시각 그대로. ② 사고 모양 재현 — dms-api·dms-controller 의 last-applied 어노테이션 image 만 `dms:d31` → `dms:d30`
  (`kubectl apply set-last-applied` — 릴리스 뒤 태그가 뒤처진 상태, 실제 image·파드는 d31 그대로)으로 되돌리고 dms-config 의
  `DMS_BATCH_SCHEDULE_KEEP_ROUNDS` 를 `"9"` 로 바꾼 뒤(설정 변경 흉내) S11 → apply 가 last-applied 만 고쳐 Deployment generation 이 90 에서
  올랐는데도 「설정이 바뀌어 다시 띄웠다: deploy/dms-api deploy/dms-controller ds/dms-agent」, 새 파드 env·CM 모두 `10`, `STAGE: S11 PASS`,
  `:preview` keep_rounds 10, 컨트롤러 로그 오류 0. 옛 설치기는 이 모양에서 agent 만 다시 띄운다(단위 테스트로 빨간불 재현 — 라이브 23:50 출력과 같음).

### ✅ 스케줄 배치 회차 보존 기본 5 → 10 d31 — **완료·배포**(2026-11-16)

사용자 지시(2026-11-16, d30 배포 뒤): 「회차기록 최근 5회차에서 10회차 보존으로 변경해줘」 — 바로 아래 d29 항목의 「최근 5회차만 남기고
나머지 정리」의 보존 수만 바꾼다(정리 규칙·경계 계산·닻·감사·상한 5000 은 그대로).

- **바뀐 것**:
  - 코드 기본 `DMS_BATCH_SCHEDULE_KEEP_ROUNDS` 5 → **10**(`config.py` 의 키 표·`Settings` 기본). 범위 1–100(밖이면 기동 거부)과 회차 경계 행
    보존 상한 `BATCH_ROUND_HISTORY_CAP` 200(= 설정 상한의 두 배)의 관계는 그대로다. base `deploy/k8s/20-config.yaml` 도 `"10"`(사이트 중립 —
    코드 기본과 같은 값).
  - 포탈: 보존 수는 이미 서버 `keep_rounds` 를 그대로 쓴다(스케줄러 머리 안내·추가/수정 창·배치 상세 배지 아래 — `keep_rounds` 가 없는 옛
    서버면 스케줄러 머리는 숫자 없는 문구, 창·배지는 안내 생략).
    박혀 있던 숫자는 사용량 상세 「실 사용량 추이」 도움말의 「보존 설정 기본 5회차」 하나였다 — 그 화면엔 서버 값이 없어 숫자 없는 문구(「최근
    보존 회차(회차 수는 배치 스케줄러 화면 안내)」)로 바꿨다. 「항목 × K > 상한」 예측 경고는 K=10 이면 항목 **501개**부터 뜬다(5 시절 1001개).
  - 문서: CLAUDE.md(배치 스케줄러 규약의 「기본 5」), ARCHITECTURE(루프 지도·모듈 표), deploy/README §12 「스케줄 회차 보존 정리」(기본 10, 보존 수를
    올리면/내리면 무엇이 되나, 501)·env 표·§14-1 화면 표, BACKLOG 파생 수치(1001 → 501, 배포 전 상한 초과 배치의 ~5일 → ~10일). 코드 주석·
    docstring 의 「기본 5」도 10 으로. 옛 항목(아래 d29 등)은 당시 기준이라 고치지 않았다.
- **기존 데이터**:
  - 올리는 쪽이라 **새로 지워지는 것은 없다** — 경계가 더 오래된 쪽으로 물러나므로, 배치마다 회차가 10개를 넘을 때까지 정리가 쉰다.
  - **이미 지운 회차는 되돌아오지 않는다**: 테스트베드 배치 e5fe 는 d29 실증에서 기록 이전 자식 8개와 기록 1·2회차 몫 4개(누적 12)가 이미
    정리됐고, 남은 자식 10개(r3–r7)는 그대로다(그 배치의 스케줄은 d29 UI 단계에서 지워져 지금은 정리 대상도 아니다).
  - 항목 **501개 이상** 스케줄 배치는 10회차만 남겨도 배치 단위 삭제 상한 5000 을 넘는다(스케줄 화면 예측 경고 — 넘는 배치 삭제는 BACKLOG
    「작업 5000개(또는 항목 10000개)를 넘는 배치의 삭제」).
  - ConfigMap 값이 코드 기본보다 우선한다: base 를 이미 적용한 사이트(CM 에 `"5"`)는 다음 S11 적용 전까지 5 그대로다. 테스트베드 라이브
    `dms-config` 도 11-16 S11(d29 실증 결함 ② 처리)로 `"5"` 가 들어가 있어, d31 이미지만으로는 5 이고 S11 을 다시 적용해야 10 이 된다.
- **테스트**: `test_config_batch`(기본 10·base CM `"10"` = 코드 기본·설정 상한 ≤ 회차 경계 보존 상한), `test_api_batch_schedules`(응답·목록·배치 요약
  `keep_rounds` 10, `prune_preview` 경계를 기본값으로 — 회차 정확히 K개(기록 이전 1 + 기록 9) = 0 · K+1 = 기록 이전 자식 · K+2 = + 1번째 기록
  회차 자식). `test_batch_round_prune` 은 K=5 를 명시하는 그대로다(회차·삭제 수 시나리오가 K=5 기준이고 경계 규칙은 K 와 무관 — 도우미에 주석).
  프런트 `batchSchedule`(K=10 에서 항목 500 → 경고 없음 · 501 → 경고, 보존 안내는 받은 숫자 그대로), `TargetSheet`(도움말에 숫자 없음),
  e2e 18 의 보존 안내 단언 「최근 10회차만 보존합니다」(실 백엔드 기본값). 돌린 것: 백엔드 관련 13파일 302 passed·4 skipped(PG 전용) +
  배포 문서·스크립트 계약 5파일 327 passed·1 skipped, vitest 5파일 84 passed, `tsc -b` 통과, e2e 18 3/3(빌드 포함, `dist/index.html` 외부 URL 0).
- **배포**(2026-11-16 KST): 포탈 빌드 d31(소스 0b5e7357, 3종, 225초) → 릴리스 dms-agent → dms-api → dms-controller → job-image 4종 모두
  Applied(live == 동봉 d31) → 오버레이 태그 d30 → d31(b81fcc53) → `sh deploy/install.sh --context dmsk8s --site testbed-k8s --only S11`
  `STAGE: S11 PASS`(23:50 — 라이브 CM `DMS_BATCH_SCHEDULE_KEEP_ROUNDS` `"5"` → `"10"`). **그러나 S11 은 ds/dms-agent 만 다시 띄우고 dms-api·
  dms-controller 는 env 5 그대로 남겼다** — 설치기 결함(바로 위 항목, 릴리스 뒤 태그 맞춤 apply 가 Deployment generation 만 올려 「이미 새
  파드」로 오판). `kubectl --context dmsk8s -n dms rollout restart deploy/dms-api deploy/dms-controller`(23:52)로 손으로 다시 띄운 뒤 확인:
  `:preview` 의 `keep_rounds 10`, api·controller env `DMS_BATCH_SCHEDULE_KEEP_ROUNDS` 10, `prune_preview` {rounds_recorded 7, would_prune 0}
  (기록 7회차 < 10 이라 정리 없음).

### ✅ 스케줄 회차 최근 5회차 보존·배치 단위 삭제 상한 5000 d29 — **완료·실증**(2026-11-16)

사용자 요청(2026-11-16, d28 출하 보고 직후): 「남겨둔것 정리해. 최근 5회차만 남기고 나머지 정리해. 배치 단위로 한번에 지우는것 상한
5000개로 늘려줘.」 — d28 보고의 「스케줄 회차마다 자식 요청이 쌓여 배치 단위 삭제 상한(2000)을 넘으면 그 배치를 배치 단위로 못 지운다 —
지금은 자동 정리 없음 + 80% 경고」에 대한 답이다. 설계는 세션 job tmp `prune/DESIGN.md`(§0~§14, 자체 비판 15건 반영).

- **① 남겨둔 것 정리**: 구현 대상이 아니다 — 18:10–18:15 KST 정리 단계가 d28 실증 잔여물(scan 배치 78b7 자식 137 · sync 배치 2d25 자식 3 ·
  픽스처 `/cephfs/managed/dms-proof-d28` 1,500 디렉터리)을 지웠다(job tmp `prune/CLEANUP.md`, 아티팩트 135개는 DMS 정리 루프가 지움). 남긴
  배치 e5fe(scan, 자식 8)는 d29 라이브 실증 재료다.
- **② 최근 5회차 보존 — 결정**:
  - 보존 수 = env `DMS_BATCH_SCHEDULE_KEEP_ROUNDS`(기본 5, 1–100 밖이면 기동 거부 — 상한 100 은 회차 경계 행 보존 상한 200 안에서 경계를
    계산할 수 있어야 해서). base `20-config.yaml` 에 `"5"`.
  - 회차 = `BatchesRepository._rescan_locked` 한 번(사람 `:rescan` · 스케줄 회차). 실패분·선택 재실행·항목 추가는 그 회차에 속한다. 경계는 새 표
    `batch_rounds`(batch_id·round_seq·started_at·floor_order·via·actor·requeued, PK batch_id+round_seq) — floor_order = 회차가 시작되는 순간
    그 배치 자식의 `MAX(commit_order)`, 상태 CAS 와 같은 트랜잭션에서 그 몸통만 남긴다(배치당 200행). 오케스트레이터 hot path 는 그대로.
  - 정확성: 회차 N 의 자식은 N+1 시작 전에 모두 커밋돼 있어(종단 배치에서만 이기고 자식 INSERT·항목 claim 이 한 트랜잭션) 경계가 정확하다.
    commit_order 는 현존 행 기준 MAX+1 이라 최대 행이 지워지면 재사용될 수 있으므로 **최신 회차의 경계 자식(닻)을 절대 지우지 않는다**
    (`upto = min(cut, anchor − 1)` — 변종 테스트가 닻 규칙을 빼면 깨지는 것까지 고정). K=1 이면 닻 1건이 다음 회차까지 남는다.
  - 옛 행: 기록 이전 자식(첫 실행·배포 전 회차 전부)은 가장 오래된 회차 하나로 보고 기록된 회차가 K개가 되는 순간에만 한꺼번에 정리한다 —
    배포 직후 대량 삭제는 구조적으로 없다(보수적).
  - 대상은 `batch_schedules` 행이 있는 배치(켜짐·꺼짐·일시정지 무관 — 스케줄을 지우면 멈춘다). 절대 지우지 않는 것: 비종단 요청·비종단 잡이
    있는 요청·조용한 창 안·어느 `batch_items` 든 가리키는 요청·보존/지금 회차·닻.
  - 실행: 새 컨트롤러 루프 `batch-round-prune`(30초, 설정 키 아님, purge_runner 가 배선된 경우만 — artifact_expiry 선례). 발사 트랜잭션엔 경계
    기록 문장 4개만 더했다. 사람 `:rescan` 회차도 같은 루프가 30초 안에 정리한다. 유지보수·drain 이면 쉰다.
  - 삭제: 기존 `_purge_locked` 재사용 + `per_request_audit=False`(PURGED_TABLES 삭제·아웃박스 그대로, 자식별 스냅숏·`('request','delete')` 생략).
    감사는 청크 트랜잭션마다 `('batch','prune_rounds', batch_id)` 1행(actor `system:batch-scheduler`, 자식별 최소 기록 — 32 KiB 넘으면 단계적으로
    줄임). 청크 ≤ 100 자식, 이 루프의 정리 대기 행(requested_by = system:batch-scheduler) ≥ 100 이면 그 틱은 쉼, 틱당 실제 청크 ≤ 10, 배치마다
    청크 하나씩 순환, 막힌 자식은 고르면서 건너뛴다(비종단 잔재가 진행을 막지 않게 — 경계 끝까지 훑고도 막힘만 남으면 `batch_round_prune_blocked`
    1시간 1회). 고르기는 경계 이하 자식을 인덱스 순서 창(500 — LIMIT 을 품은 FROM 서브쿼리)으로 자른 바깥에서 판정하고 호출당 5000행까지만
    훑는다 — 다 못 훑으면 배치별 커서(`batch_round_prune._SCAN_FROM`)로 다음 청크가 이어 본다(검증 지적: 판정을 한 WHERE 에 두면 PG16 이
    Bitmap Heap Scan + top-N 정렬로 경계 이하 **전부**에 판정을 돌려, 경계 이하 2.5만에서 고르기 80ms·청크 ~100ms — 청크 비용이 대기량에
    비례했다).
  - 잠금 순서: 정리 = requests(request_id 순) → data_jobs → batch_schedules(맨 끝, 카운터). batch_rounds 는 잠그지 않고 읽는다(과제 문구의
    「스케줄 → 회차 → 요청」 순서는 배치 단위 삭제와 역순이라 교착 — 따르지 않았다). `BATCH_DELETE_TABLES` = batch_items → batches → batch_rounds
    → batch_schedules → batch_schedule_runs, 두 삭제 경로가 batch_rounds 를 스케줄보다 먼저 지운다.
  - 스케줄 행에 `pruned_at`·`pruned_total`(CREATE TABLE·`_ensure_columns` 양쪽, NULL = 아직 정리 없음). 정리는 `updated_at` 을 밀지 않는다(밀면
    발사·기록 CAS 가 그 회차를 lost 로 본다).
- **③ 상한 5000 — 결정**: `MAX_BATCH_DELETE_CHILDREN` 2000 → **5000**, 항목 10000 그대로, 새 **호출당 자식 합 예산 `MAX_DELETE_CALL_CHILDREN`
  = 10000**(넘는 배치는 새 사유 `batch_delete_call_budget` 로 건너뜀 — reasonCodes.json·REASON_MESSAGES 양쪽 등록; 미등록 스토리지 정리는 넘는
  배치를 그 회차에서 빼고 `remaining_untried` 로 다음 회차에). 이유: 5000 × 배치 10 이면 RLock 을 35–40초 쥐어 readiness 를 연속으로 놓친다.
  `list_summaries` 의 성공 scan 판정을 EXISTS → 스칼라 서브쿼리(`… LIMIT 1) IS NOT NULL`)로 — cap 5000 에서 PG16 이 Hash Semi Join + data_jobs
  전체 Seq Scan 으로 바뀌는 회귀를 막는다(소스 형태 테스트).
- **API**: 스케줄 행에 `keep_rounds`·`pruned_at`·`pruned_total`(`child_count_limit` 은 5000). `:preview` 는 `keep_rounds`·`child_count_limit` 을 늘,
  `batch_id` 가 있으면 `prune_preview {rounds_recorded, would_prune, would_prune_capped}`(실제 정리와 같은 `_round_cut`). 배치 목록·상세 요약의
  `schedule` 에 `pruned_*`·`keep_rounds`. 작업 목록(관리자)의 배치 자식 행에 `batch_request_count_limit`·`batch_item_count_limit`. `:rescan` 응답은
  그대로(내부적으로 `batch_rounds` via rescan, actor = 세션 사용자).
- **포탈**: 스케줄 화면 머리·추가 창·배치 상세 배지 아래에 「최근 K회차 보존」 안내, 스케줄 행 「정리」 열(「정리 누적 N건 · 마지막 …」 /
  「아직 정리 없음」 — null ≠ 0), 추가 창의 「저장하면 … 작업 약 N개가 곧 정리됩니다」·「항목 × K > 상한」 예측 경고(저장은 막지 않는다),
  80% 경고는 서버 `child_count_limit` 기준(= 4000). 작업 목록은 배치당 상한을 행의 서버 값으로 판정(`batchGroups.ts` 의 `MAX_BATCH_CHILDREN`·
  `MAX_BATCH_ITEMS` 삭제 — 숫자가 없으면 「정보 없음」, fail-closed)하고 고른 배치의 작업 합 > 10000 이면 툴바가 잠근다
  (`MAX_DELETE_CALL_CHILDREN` 거울). 사용량 상세 「실 사용량 추이」 도움말에 스케줄 배치 scan 이 최근 회차만 남는다는 한 줄.
- **로컬 실측**(`tests/perf_batch_delete.py` — 합성 세계 단건 1만 + 자식 5500 배치 20 = 요청·잡 12만, 측정 배치는 그 위에 새로. 합성 자식은
  전이·결과·이벤트·plan 이 없어 조사 B(PG16 unix 1.86–1.99초·sqlite 0.82–0.86초)보다 가볍다):
  | 항목 | sqlite | PG16(unix 소켓) |
  |---|---|---|
  | `delete_batch` 자식 2000 / 5000(합성 하한 — 상한 근거는 조사 B 1.86–1.99초) | 0.17초 / 0.40초 | 0.52초 / 1.32초 |
  | 5000 × 2 연속 — readyz 흉내 최대 대기 | 0.80초 — 0.36초 | 2.71초 — 1.31초 |
  | `list_summaries` 성공 scan 갈래 자식 4000 / 4500 / 5000 — 옛 EXISTS | 5.9 / 6.9 / 7.4ms | 29.3 / 29.2 / 11.0ms(Hash Semi Join + Seq Scan) |
  | 같은 갈래 — 새 스칼라 서브쿼리 | 5.8 / 6.7 / 8.0ms | 12.5 / 12.8 / 11.6ms(SubPlan + Index Scan — 목표 ≤ 15ms) |
  | `list_summaries([1배치], cap 5000)` 전체 | 7.8 / 8.6 / 9.9ms | 14.3 / 15.7 / 17.0ms |
  | 정리 청크 100(`prune_round_children`) — 최선(자식이 정확히 100인 배치) | 8ms(문장 117 — 같은 100자식 `delete_batch` 623) | 30ms |
  | 같은 청크 — 경계 이하 대기 2.5만(자식 3만 배치, 커서로 5청크 중앙값; 옛 한 문장 고르기는 PG 청크 ~100ms) | 15ms | 18ms |
  | 같은 대기 + 앞 2만이 막힘(8청크 — 처음 4청크는 예산만 훑고 0건, 그다음 100건씩) | 15ms(최대 19) | 13ms(최대 19) |
  테스트베드 왕복 0.07ms 환산으로 5000 배치 하나 ≈ 3.5–4초(조사 B) — readiness 프로브 1회를 놓칠 수 있다(연속 3회가 아니면 파드는 빠지지 않는다).
  expected 를 작게 속여 호출당 예산에 끼워 넣은 배치는 삭제 전 `batch_changed` 로 끝나고 그 앞의 잠금·읽기만 치른다(PG 자식 5000 배치 ≈ 22ms,
  10개 ≈ 0.2초 — `within_call_budget` docstring).
- **테스트**: 새 `tests/test_batch_round_prune.py`(31 — 5·6회차 경계·기록 이전 행·재실행 자식·비종단/조용한 창/항목 참조 보호(잠금 직전 경합
  훅 포함)·K=1 닻·빈 보존 회차 닻·commit_order 재사용과 닻 규칙 변종 자기 검증·고르기 창·예산·커서(막힌 앞자리 뒤로 이어 감)·청크 100/100/50·대기 상한 100/70→30/다른 actor·공평 순환·
  대상 범위·유지보수/drain·고아 없음 + finish·감사 요약·32 KiB 접기·스케줄 칸과 fire CAS·스케줄 삭제 경합·예외 격리·동시 rescan·예측=실제·
  루프 배선, PG 동시성 4종 — 정리↔배치 단위 삭제 양방향·정리가 스케줄을 쥔 동안의 fire·정리 둘 동시), 기존 갱신(`test_repo_batch_schedules`
  회차 경계·잠금 순서 스파이에 batch_rounds·정리 청크 순서, `test_api_batch_schedules` 응답·preview·그물 순서, `test_migrations` 표 등식·
  `_ensure_columns`, `test_config_batch`, `test_api_request_delete` 예산 4, `test_api_usage_unregistered` 예산, `test_api_request_list_batches`
  limit·스칼라 모양·판정 동치, `test_repo_request_purges` 문장 예산·삭제 집합 동일, `test_batch_delete_limits_mirror` 재작성,
  `test_control_plane_static`·`test_cli` 배선). 프런트: batchGroups·JobsList(합계 잠금·사유 문구·5000 문구)·batchSchedule·BatchSchedulesPage·
  BatchScheduleDialog·BatchDetail.schedule·TargetSheet, e2e 18 에 보존 안내·정리 열(수기 게이트).
- **남은 위험**(BACKLOG 「포탈 개선·배치 스케줄러 후속」·「작업(요청) 삭제 후속」): 정리 경로의 자식별 감사 스냅숏 없음(결과 메시지·전이 전문
  소실) · 배치 단위 삭제의 「자식 수 = 버전」 CAS 가 근사(최대 commit_order CAS 강화안) · 배포 전 이미 상한을 넘은 배치는 기록 K회차까지 못
  지움 · 항목 1001개 이상 스케줄 배치는 정리 뒤에도 상한 초과(경고만) · 정리 처리량 = 아웃박스 처리량 · 5000 배치 하나 ≈ 3.5–4초 API 정지 ·
  미등록 스토리지 정리 포탈 연속 회차의 readiness(기존 성질) · 사용량 분석의 과거 scan 점·수치가 줄어듦(의도) · 롤링 배포 중 옛 파드가 경계 없이
  시작한 회차는 앞 회차와 합쳐져 더 오래 보존(안전한 방향).
- **커밋**: 9166b8e2(기능), 68bf89b7(testbed-k8s 오버레이 d28 → d29).
- **배포**: 포탈 빌드 d29(210초) → 릴리스 4종(dms-agent·dms-api·dms-controller·job-image) 모두 Applied(라이브 api·controller `dms:d29`, agent
  `dms-agent:d29`, 둘 다 재시작 0). 새 표·칸은 기동 때 만든다. 새 설정 키 `DMS_BATCH_SCHEDULE_KEEP_ROUNDS` 는 라이브 ConfigMap 에 아직 없다(아래 결함 ②).
- **테스트베드 정리**(d28 잔여물, 18:10–18:15 KST — job tmp `prune/CLEANUP.md`): 배치 78b7(자식 137)·2d25(자식 3)를 배치 단위 삭제(감사 822–963),
  픽스처 `/cephfs/managed/dms-proof-d28` 1,500 디렉터리(파일 4,784개·175,716,256 B)를 k8s-w1 SSH 로 삭제, 아티팩트 135개는 손대지 않고
  request-purge 루프가 지웠다(pending 140 → 0). cephfs-dms 사용량 대상 134 → 3(`e2e/dest`·`e2e/growth`·`e2e/victim-protected`).
- **라이브 실증**(d29, dmsk8s, 관리자 mason 세션 API·실 Chrome, kubectl 은 `--context dmsk8s` 읽기만, DB 는 CNPG 복제본 `dms-pg-1` 에서 읽기 전용
  SELECT 만 — 원자료 job tmp `prune/live29/`, 절은 `prune/PROOF-d29.md` P1·U1). 대상은 scan 배치 e5fe(cephfs-dms, 항목 2 `e2e/growth`·`e2e/dest`,
  자식 8 = 배포 전 회차 4개 몫) 하나뿐이다:
  - **P1 API·루프**(21:22–21:46 KST):
    - 배포 확인: 리스 `loop:batch-round-prune` 를 새 컨트롤러 파드가 쥐고 갱신 중(90초 리스), 컨트롤러 로그 30분 0줄, `batch_rounds` 표와
      `batch_schedules.pruned_at`·`pruned_total` 칸 있음. 작업 목록 관리자 응답의 배치 자식 행 `batch_request_count_limit 5000`·
      `batch_item_count_limit 10000`, 스케줄 생성 응답·`:preview` 의 `child_count_limit 5000`·`keep_rounds 5`, preview `prune_preview {rounds_recorded 0,
      would_prune 0}`.
    - 스케줄 생성: scan 이라 동의 없이 201(`2db35cc2…`, monthly, next 2026-11-30T18:00Z), `pruned_total` 은 0 이 아니라 null.
    - 회차 7개(사람 `:rescan` 6번 + 스케줄 회차 1번): `batch_rounds` 7행 — via rescan·actor mason 6행, via schedule·actor `system:batch-scheduler`
      1행. floor_order 248 → 250 → … → 260 이 매번 직전 회차의 마지막 자식 commit_order 와 정확히 같다. 모든 `:rescan` 은 200 `requeued 2`.
    - r1–r4(기록 1–4개)는 하나도 지우지 않았다(prune 감사 0행, `pruned_total` null 유지, 자식 8 → 16, r4 뒤 preview `{4, 0}`).
    - r5(기록 5개)에서 경계 기록 전 옛 자식 8개(commit_order 4·5·103–106·247·248 — Conflict 1·Succeeded 7, 잡 7)가 `:rescan` 18초 뒤 한 번에
      정리됐다(감사 971). 그 뒤로는 회차마다 가장 오래된 회차 몫만: r6 → r1 몫 249·250(29초 뒤, 972), 스케줄 회차 r7 → r2 몫 251·252(발사와 같은
      초, 975). 셋 다 30초 루프 주기 안이다.
    - 최종 자식 10 = 정확히 최근 5회차 몫(commit_order 253–262 = r3–r7, 회차당 2개, 모두 Succeeded). 닻 규칙: upto 248·250·252 =
      `min(cut, anchor − 1)`, 닻 자식 256·258·260 은 지워지지 않았다. 정리 순간마다 진행 중이던 지금 회차 자식(257 Planned/잡 Running, 260·261
      Pending)은 무접촉, 감사 `skipped {}`.
    - 감사 `('batch','prune_rounds', e5fe)` 3행: actor `system:batch-scheduler`, deleted 8/2/2·job 7/2/2·cut_round_seq 1/2/3·rounds_recorded 5/6/7·
      upto/anchor 248/256·250/258·252/260, keep_rounds 5, truncated false(2,171/862/862 B). 자식별 `('request','delete')` 감사는 0행. 요약 이벤트
      `batch_round_prune_done` 3건, 막힘 경고 0.
    - 스케줄 `updated_at` 은 정리로 밀리지 않았다(사람의 PATCH·disable 때만 바뀜). 스케줄 회차 발사 CAS 는 lost 없이 fired(run_id 6, 지연 0초).
    - 정리 아웃박스(requested_by `system:batch-scheduler`) 8 → 7 → 0, 2 → 0, 2 → 0, `request_purged` 이벤트는 정리 2–19초 뒤. 지운 잡 11개의
      `/dms_shared/dms/artifacts/<job_id>` 11/11 사라짐(남은 자식 잡 10개는 10/10 그대로), 지운 잡의 vcjob·Pod 0, DB requests·data_jobs·events·
      results 에 지운 id 행 0. 예외(기존 동작): 10-25 잡 `773e8bc3…` 은 옛 base 사본이라 `artifact_left_at_old_base`(이벤트 1387)로 설계대로 남았다.
    - `pruned_total` null(r1–r4) → 8 → 10 → 12, `pruned_at` 12:42:00Z, child_count 10 / 5000, 배치 summary `schedule.keep_rounds 5`. 마지막 preview
      `{rounds_recorded 7, would_prune 0}` 으로 실제와 일치.
    - 사용량 scan-history 점: growth 4 → 8 → 6, dest 4 → 8 → 5(scan_count 도 같음). growth 의 e5fe 밖 scan `61cc10b4…` 는 그대로라 first_scan_at
      이 10-25 로 유지, dest 의 first_scan_at 은 r3 자식(12:31:06Z)으로 당겨졌다(의도).
    - 스케줄 삭제 뒤 정리 정지는 자식을 늘리지 않으려고 코드로만 확인했다(`prune_targets` 가 `FROM batch_schedules`, 청크는 스케줄 FOR UPDATE
      재확인에서 행이 없으면 `schedule_gone`).
  - **U1 UI**(실 Chrome headless, 1440·390, 21:50–21:59 KST — 페이지마다 GET 이 아닌 요청을 끊는 가드, `:preview` 와 마지막 DELETE 만 통과):
    - 화면 11종 × 폭 2 = 22개, 스크린샷 32장에서 콘솔 오류·실패 요청·HTTP≥400·문서 가로 넘침 0(감사 화면의 `null` 1건은 null≠모름 표기 —
      결함 아님).
    - 스케줄러: 머리 안내 「최근 5회차만 보존 … 스케줄을 꺼도 정리는 계속되고, 스케줄을 지우면 멈춥니다」(서버 keep_rounds), 정리 열 「정리
      누적 12건 · 마지막 2026-11-16 21:42:00」(390 은 표 래퍼 안 가로 스크롤), 실행 기록 창 1행. 수정 창은 preview `{7, 0}` 이라 「곧 정리될 작업」·
      「항목 × 5회차」 경고 0(정상) — 가로채기 I1(브라우저에서 preview 응답만 would_prune 4·limit 5)로 두 줄이 그려지는 것을 확인(390 넘침 0).
    - 배치 상세 배지 「스케줄 꺼짐 — 매일 21:42 (KST)」 아래 「최근 5회차만 보존 — 전체 재실행하면 가장 오래된 회차가 정리될 수 있습니다」.
    - 작업 목록: 행 limit 5000 → 배치 체크박스 10/10 활성, 툴바 「배치 1개 선택됨(작업 10개 · 성공 scan 10개)」, 삭제 창은 열고 닫기만
      (`requests:delete` 0). 가로채기 I2: 행 limit 을 5 로 → 「삭제 상한 초과」로 잠김, 키 삭제 → 「정보 없음」(fail-closed) — 판정은 행의 서버 값.
      배포 번들에 「2000개」 0건.
    - 사용량 경로 시트 `e2e/growth`(스캔 이력 6건)의 「실 사용량 추이 설명」 도움말에 보존 한 줄.
    - UI 로 스케줄 삭제(2단 확인 → DELETE 200, 12:57:18Z) → 빈 목록, 감사 977, 추가 창에서 e5fe 를 다시 고를 수 있음(preview `{7, 0}` —
      `batch_rounds` 7행은 남음), 그 스케줄의 실행 기록 1 → 0, 977 뒤 prune_rounds 0, 컨트롤러·api 로그 0줄.
  - 라이브로 못 본 것: 꺼진 스케줄에서 정리가 계속되는지(스케줄 회차의 정리가 발사와 같은 초에 끝나 끄기보다 먼저였다 — 코드로만), upto
    이하 비종단 잔재 건너뛰기, 조용한 창이 정리를 미루는 순간, BACKLOG_MAX·청크 100 상한 — 라이브에 생기지 않아 단위·PG 테스트 몫.
- **실증 결함**: must 0, should 5건(실증 단계에서는 고치지 않았다):
  - ① 정리 감사(`batch / prune_rounds`)의 자식 기록 `run_as_root` 가 구조적으로 늘 null(라이브 14행 전부) — 배치 자식 payload 에 그 키가 없다
    (배치 자식의 root 근거는 `privilege_policy` 의 배치 자식 판정). 남길 가치가 있는 것은 payload 의 `owner_username`. 설계 §3.3-6 예시와도 다르다.
  - ② 운영: 라이브 ConfigMap `dms-config` 에 `DMS_BATCH_SCHEDULE_KEEP_ROUNDS` 가 없다(포탈 릴리스는 이미지만 바꾼다 — CM 은 d19 이후 미적용,
    `DMS_JOB_IMAGE` 가 d19). 코드 기본 5 와 같아 지금 동작 영향은 없지만, 다음 `sh deploy/install.sh --context dmsk8s --site testbed-k8s --only S11`
    전에는 CM 으로 보존 수를 바꿀 수 없다(설계 §11-0 확인 항목과 어긋남).
  - ③ 스케줄 DELETE 라우트 감사(`batch_schedule / delete`, 라이브 977)의 before 에 `pruned_total`·`pruned_at` 이 없다 — 배치 삭제 경로
    `_audit_schedule_delete` 는 싣는다(같은 감사 행인데 경로에 따라 모양이 다름). 누적은 prune_rounds 감사 합(8+2+2 = 12)으로 복원 가능.
  - ④ 스케줄 행 「삭제」 title 「회차 기록도 함께 지웁니다」의 「회차 기록」은 실행 기록(`batch_schedule_runs`)이라, 같은 화면 보존 안내의 「회차」
    (`batch_rounds` — 스케줄을 지워도 남는다)와 헷갈린다.
  - ⑤ 외형: 정리 칸 시각이 하이픈에서 꺾이고(1440 「마지막 2026-11-」/「16 21:42:00」), 새 안내 줄에 break-keep 이 없어 390 에서 어절 중간에서
    꺾인다(d28 기존 문구도 같은 상태 — 저장소 관례 DeleteRequestsDialog 의 break-keep 과 어긋남).
- **테스트베드에 남긴 것**: 배치 e5fe(Completed, 자식 10 = commit_order 253–262·잡 10·vcjob 10 — vcjob 은 DMS GC 몫)와 `batch_rounds` 7행, 감사
  970–977. 스케줄은 0건(2db35cc2 는 UI 단계가 지웠다). 옛 base 사본 k8s-w1 `/cephfs/dms/artifacts/773e8bc3c05c4917ab004f5a95057147` 은 손대지
  않았다. 클러스터 리소스(ConfigMap 포함)·계정·스토리지·sync 쌍·sync/rm 배치·current-context(dms)는 바꾸지 않았다.
- **실증 뒤 다듬기(d30)** — 위 실증 결함 ①③④⑤(②는 운영 — 다음 설치기 S11 적용 몫):
  - ① 정리 감사 자식 기록에서 `run_as_root` 칸을 뺐다. 대신 자식 payload 에 얼린 `owner_username`(None = 지정 없음)과 `dest_owner`
    (`{rule, owner}` 만 — None = 해당 없음)를 `_ID_MAX`(64) 로 접어 남긴다(`request_purges._prune_summary`). 32 KiB 축소 첫 단계(잡 빼기)에서도
    두 칸은 남는다. 테스트 픽스처의 배치 자식 payload 에서도 `run_as_root` 를 뺐다(실제 모양).
  - ③ 스케줄 DELETE 라우트와 배치 삭제 두 경로의 `('batch_schedule','delete')` before 를 한 도우미 `batches.schedule_delete_audit_before` 로
    맞췄다(pruned_total·pruned_at·last_run_at·last_reason_code·paused_reason_code·run_count·deleted_with_batch). 세 경로 키 집합이 같다는 것과
    pruned_total null ≠ 0 을 `test_api_batch_schedules` 가 고정한다.
  - ④ 낱말 통일: 스케줄 행 「삭제」·「삭제 확인」 title = 「실행 기록도 함께 지웁니다 — 보존 회차 셈과 이미 남은 작업은 그대로, 정리는
    멈춥니다(감사 기록은 남습니다)」(`SCHEDULE_DELETE_HINT`). 배치 삭제 창 두 곳(배치 상세·작업 목록 배치 단위)은 「…과 실행 기록·보존 회차
    기록도 함께 삭제됩니다」이고, deploy/README §14-1 화면 표도 같은 낱말로 고쳤다.
  - ⑤ 390 줄바꿈: 「정리」 칸을 누적 / 「마지막 <시각>」 두 줄로 나누고 둘째 줄 전체를 `whitespace-nowrap` 로 묶었다(시각만 묶으면
    390 에서 「마지막」과 시각 사이 공백에서 꺾여 세 줄이 됐다 — 리뷰 실 Chrome 실증; 시각 span 은 `tabular-nums` 만). 칸·머리 안내 목록·
    추가/수정 창 「회차 보존 안내」·배치 상세 보존 줄에 `break-keep` 를 넣었다. e2e 18 의 375 시나리오가 목록 응답의 그 행에만
    pruned_total 1234·pruned_at 을 page.route 로 합성해 「마지막」 글자와 시각 조각이 같은 줄이고 시각이 한 줄인지 본다(시각만 묶은
    옛 모양에선 16px 어긋나 빨간불을 확인). e2e 03-layout·18-batch-scheduler 7/7 통과(넘침 단언 그대로).

### ✅ 포탈 개선 — 사용량 전체 목록·최신 scan 차트 · 배치 대상 스토리지 열·큰 배치 상세 쪽 넘김·검색 · 배치 스케줄러 d28 — **완료·실증**(2026-11-16)

사용자 요청(2026-11-16, 입구 Traefik 작업 뒤) 세 건(요지):
- **#66 사용량 분석**: 스토리지를 누르면 상위 100개만 보인다 → 몇백~몇천 개여도 버벅이지 않는 전체 목록. 작업 화면(배치 scan 항목 펼침)의
  「데이터 온도 히스토그램」·「파일 크기 분포」를 사용량 분석에서도 최신 scan 기준으로 같은 모양으로(「다르게 보일 이유가 없다」).
- **#67 배치 작업**: 목록의 「작업」 오른쪽에 「대상 스토리지」 열. 상세는 항목이 수백 개 이상(scan 등)이면 느려지고 가끔 「응답 없음」이다 —
  전체 배치를 한 번에 끌어오는 탓으로 보인다 → 해결. 사용량 분석처럼 상세에 검색.
- **#68 배치 스케줄러**: 작업 메뉴에 「배치 스케줄러」 — 배치를 지정하고 주기마다 확인해 이전 회차가 끝났으면 다시 실행.

설계는 세션 job tmp `portal3/DESIGN.md`(§0~§6). 구현은 단계 0(공용 부품) → A(#66)·B(#67)·C1(#68 새 파일) 병행 → C2(#68 공유 파일, B 뒤) →
전체 게이트 → 리뷰 → 최종 게이트 순서였다(파일 소유 분담 — 같은 파일을 두 구현자가 동시에 만지지 않게).

- **dms-ssc 와 정렬**: dms-ssc 세션이 같은 날 같은 세 건을 받았다(스케줄러 결정은 그 세션의 사용자 결정). 이 라인을 구현할 때 dms-ssc 는 그것을
  미 push WIP 로 갖고 있었고, 그 뒤 dms-ssc d197/d198 로 push·출하했다(origin/dms-ssc e8786abe). dms-k8s 는 push 를 기다리지 않고 구현하되
  **테이블·컬럼·API 경로·응답 키·사유 코드·컴포넌트 파일 이름을 그 WIP 와 맞췄다** — 나중 양쪽 cherry-pick 이 기계적으로 되게(push 된 판과 다시
  대조한 차이는 BACKLOG 「dms-ssc 역이식 후보」·「dms-ssc 에서 골라 올 것 — 스토리지 사용 중 가드」). dms-k8s 차이는 셋: ① LDAP 이 없어 자격을 잃은 배치 요청자의 자식은 강등이 아니라
  `privileged_not_authorized` 로 거부된다 → 스케줄러가 미리 멈춘다 ② 사용량 `/targets` 봉투(`offset`·`has_more`·`pageable_count`)와 Pager null 모드
  ③ 검색이 경로와 PV 이름을 함께 본다(사용량 검색과 같은 뜻).
- **결정(#68 — dms-ssc 사용자 결정 그대로)**:
  - 회차 = 같은 배치 **전체 재실행**(사람의 `:rescan` 과 같은 몸통 `BatchesRepository._rescan_locked`).
  - 회차 시각에 배치가 종단(Completed·Cancelled)이 아니면 **그 회차를 건너뛰고 상태별 사유를 기록**한다. 밀린 회차를 끝난 뒤 이어서·몰아서
    돌리지 않는다.
  - scan·sync·rm 모두. sync·rm 은 회차마다 기존 확인 게이트를 거친다(배치 자동 확인이 켜졌으면 자동, 아니면 PreviewReady 에서 사람 확인 대기).
    스케줄러는 confirm·`auto_confirm_by` 쓰기·Running 직행을 하지 않는다. 단건 sync·rm 자동 컨펌 금지는 그대로다(스케줄 대상은 배치뿐).
  - rm 스케줄은 무장할 때 「되돌릴 수 없음」 동의 — delete 를 켠 sync 도 같은 규칙(더 엄격한 쪽, 구현 시점 dms-ssc WIP 와 같다)이고, 동의 순간의 배치
    지문을 남긴다.
  - 주기 = **KST 프리셋 4종**: 매일 HH:MM · 매주 요일(여러 개) HH:MM · 매월 N일 HH:MM(그 달에 없으면 말일로 당김) · N시간마다(1·2·3·4·6·8·12,
    기준 HH:MM 격자). **cron 입력 없음**.
  - **회차 자식 자동 정리 없음**(설계의 열린 결정 — 기본값) + 스케줄 화면의 80% 경고(배치 단위 삭제 상한 2000 의 80%).
  - 그 밖 기본값: 배치당 스케줄 1개(`batch_id UNIQUE`), 놓친 회차(지연 > 유예 = min(1시간, 주기/2))는 missed 로 한 번 기록하고 다음 슬롯,
    「지금 실행」 버튼 없음(상세의 「전체 재실행」이 그 일), 루프 간격은 오케스트레이터와 같은 5초(새 설정 키 없음)·틱당 20건.
- **#66 사용량 분석**:
  - `/api/admin/usage/targets` 에 `offset`(0~10억, 밖은 422). 봉투에 `offset` 메아리·`has_more`·`pageable_count`(이 정렬로 쪽을 넘겨 닿을 수 있는
    행 수 — SQL·비지표 정렬 = matched_count, 지표 정렬(bytes·files·hot) = 창 크기 min(matched, 1만), PV 경로 = 판정 결과 수). null 은 PV 이름
    검색이 창을 넘어 경로 일치를 끝까지 읽지 못한 갈래(`_with_path_hits` 첫 갈래)뿐이고 0 으로 접지 않는다 — 설계는 「PV 창 넘침 = null」이었지만
    PV 필터만 넘친 경우 닿을 수 있는 행이 정확히 창 안이라 수를 싣는다.
  - `scan_target_heads(offset=)` 는 offset > 0 일 때만 바깥 `ORDER BY`·`LIMIT` 뒤에 `OFFSET` 을 붙인다 — offset 0 이면 문장이 글자 하나 바뀌지
    않는다(`test_usage_equivalence`). 범위 밖 offset 은 행이 0 이라 `COUNT OVER` 가 사라지므로 같은 필터·같은 문장 모양으로 limit 1 을 한 번
    더 물어 총수를 낸다(범위 밖 쪽에서만). 지표 정렬은 창 끝을 넘는 쪽이면 지표 계산을 건너뛴다. `/targets/export`·옛 `scan-targets` 는 그대로.
    `scan-history` 에 `latest_job_id`(가장 최근 성공 scan — 리포트를 읽었는지와 무관, 없으면 null).
  - 포탈: 공용 `components/ui/Pager.tsx`(dms-ssc 와 같은 props + k8s 확장 `pageCount: null`·`hasMore` — 쪽 수 모름), 쪽당 100행, 주소
    `?page=`(정렬·검색·필터·스토리지가 바뀌면 1쪽, 쪽 넘김 = push, 범위 밖 = 마지막 쪽으로 replace — 그동안 빈 상태 대신 스켈레톤). 건수 문구
    「1–100 / 전체 1,234개」·「필터에 맞는 N개 중 101–200」(옛 「상위 100개 표시」 삭제). 배포 틈: offset > 0 을 보냈는데 메아리가 없거나 다르면
    `UsageContractError('page_unsupported')` — 롤링 중 옛 파드가 offset 을 무시하고 1쪽을 주면 「2쪽」이 1쪽 행을 보이는 조용한 거짓이 된다(라이브
    d27 이 실제로 무시함을 확인). 경로 시트는 「목록 {offset+idx+1}/{pageable}번째」, 쪽 경계의 이전/다음은 이웃 쪽을 prefetch 해 쪽 번호와 행을
    한 번에 replace 한다.
  - 차트: 공용 `features/usage/ScanHistograms.tsx`(`TempHistogram`·`SizeHistogram`·`histBars`·`tempAxes`… — 코드는 구현 시점 dms-ssc WIP 와
    diff 0, 주석만 다름; push 된 origin/dms-ssc e8786abe 와도 주석 줄 밖은 diff 0)를 배치 항목 펼침(`ItemScanStats` — 국소 사본 삭제)·시트 「최신 scan」·스토리지 합계 「파일 크기 분포」가 함께 쓴다. 온도 축
    (atime·mtime·ctime)은 「데이터 온도 추이」와 한 상태다. **null≠0 교정**: 값이 하나라도 빠진 히스토그램은 막대 대신 「모름」이다(옛 `toBars` 는
    빠진 버킷을 조용히 버려 누적 % 가 부풀었다). 시트는 `latest_job_id` 로 snapshot 을 골라, 최신 리포트를 못 읽었으면 옛 scan 을 「최신」으로
    그리지 않고 「최신 scan 리포트를 읽지 못했습니다」를 보인다. 옛 `SizeDistribution`(+`lib/sizeBuckets` 의 두 번째 라벨 규칙) 삭제. 설계의
    「스토리지 합계는 BACKLOG 보류」는 사용자의 「다르게 보일 이유가 없다」로 뒤집었다.
- **#67 배치 목록·상세**:
  - 목록 「대상 스토리지」 열(「작업」 바로 오른쪽): `GET /api/admin/batches` 행마다 `storage`·`source_storage`·`destination_storage` 를 **첫 항목**
    (`ORDER BY seq LIMIT 1` — 단건 삭제가 seq 를 다시 매기지 않는다) payload 에서 읽을 때 파생한다(파생 테이블로 고른 n 행만, 문장 하나, 스키마
    변경 없음 — `domain.validate_batch` 가 모든 쓰기 경로에서 한 배치에 스토리지 한 종·sync 는 한 쌍만 허용한다). 원문 payload 는 싣지 않고 모르면
    null(「—」), sync 는 「출발 → 도착」, td 는 텍스트만.
  - 상세 view 분리 `GET /api/admin/batches/{id}?view=summary|items|payloads|all`(dms-ssc 와 같은 모양). 2.5초 폴링은 summary(머리 — `item_total`·
    `status_counts`(6상태 0 포함)·`first_payload`·스토리지 세 키·`executing_count`·`direct_tools`·`schedule`, items 없음)와 items(`{total, offset,
    limit, items, filters}` — limit 기본 100·상한 500(밖은 접음), 그 쪽 seq 에만 상세 조인)뿐이다. payloads 는 「현재 항목 CSV」·rm 겹침 창을 열 때,
    all 은 **확인 창을 열 때 1회**(rm 「삭제 대상 전부 나열」·감사 요약 일치를 쪽 데이터로 줄이지 않게). view 없음은 옛 응답 그대로 — 롤링 중 옛
    탭의 확인 창이 「0 / 전체 0」으로 열려 root 삭제를 맹목 승인하는 일을 막는다.
  - 검색 q(새 `batch_item_search` — k8s 전용 파일로 나눠 cherry-pick 충돌 회피): `search_tokens`(공백 분리·최대 8토큰·화살표 구분자), 토큰마다
    대소문자 무시 부분 일치·토큰 사이 AND. 칸 = 경로 + 그 경로의 PV 이름(sync 는 출발·도착 합침, PV 를 담을 수 없는 스토리지는 경로만, 스토리지
    이름은 칸이 아님). 상태 필터는 정확 일치(모르는 값은 빈 결과).
  - 포탈: `useBatch` = summary(옛 파드의 전체 응답은 `headerOf` 로 정규화), `useBatchItems`(지금 쪽만), `useBatchPayloads`, `useBatchAll`. 행은
    `BatchItemRow`(React.memo), 선택은 Set, 「이 쪽 전체 선택(n개)」. 일괄 동작은 `visibleSelected`(선택 ∩ 지금 쪽에서 고를 수 있는 seq)만 보낸다 —
    쪽·검색·상태가 바뀌거나 폴링으로 쪽 내용이 바뀌면 선택을 줄이고 무장을 푼다(보이지 않는 선택이 확인한 것과 다른 집합을 지우거나 재실행하지
    않게). 검색칸(300ms 디바운스·한글 조합, 「경로·PV 이름 검색」, 강조), 상태 칩 7개(0 도 표시), 주소 `?page=&q=&status=`, Pager 는 표 래퍼 밖.
  - 같이 고친 것: `:rescan` 라우트는 읽기 → 리셋 → 무가드 `set_status` 라 사이에 낀 재실행·취소·회차를 Previewing 으로 덮을 수 있었다 → 상태 CAS
    `rescan_if_terminal`(배치 상태 CAS 뒤 집합형 항목 리셋 한 문장 — 5,000 항목 = 5,000 문장이던 것). 지면 409 `batch_not_rescannable`, 응답 그대로.
- **#68 배치 스케줄러**:
  - DB: 새 테이블 `batch_schedules`(kind·spec JSON·enabled·`next_run_at`(UTC, 꺼짐이면 NULL)·`armed_by`·`ack_fingerprint`·last_*·
    `paused_reason_code`)·`batch_schedule_runs`(scheduled_for·at·outcome fired|skipped|missed|paused·reason_code·batch_status·`requeued`(fired 만 —
    건너뜀은 NULL, 0 아님)·prev_*), 인덱스 3. 새 테이블이라 CREATE 만(`_ensure_columns` 무변경, `_ensure_pairs` 53 그대로)·`test_migrations` 등식.
    회차 이력은 스케줄당 200건. request_id·job_id 열이 없어 요청 삭제 그물 밖이 정상이고, 대신 **배치 삭제 목록** `BATCH_DELETE_TABLES`(순서 =
    잠금 순서 batch_items → batches → batch_schedules → batch_schedule_runs)와 그물을 새로 두었다 — 배치 삭제 두 경로(`BatchesRepository.delete`·
    `RequestPurgesRepository.delete_batch`)가 같은 트랜잭션에서 스케줄·이력을 지우고 감사 행(`batch_schedule/delete`, deleted_with_batch·run_count)을
    남긴다. 8개 경로의 잠금 순서를 PG 스파이 테스트로 고정했다.
  - 규칙(`batch_scheduler` — 라우트·컨트롤러 공용 순수 모듈): KST 고정 +9(zoneinfo 없음 — 이미지 tzdata 의존 없음, 서머타임 없음),
    `validate_schedule`(키 집합 정확, bool 은 정수가 아님), `next_run_after`(**엄격히 뒤**의 첫 슬롯 — 시계가 뒤로 가도 같은 슬롯을 두 번 쏘지
    않는다), `preview_runs`(포탈 「다음 5회」 — 포탈은 시각을 계산하지 않는다), `confirm_wait_note`.
  - 회차 판정(처음 걸리는 것이 결과 — 순서 원문 `batch_scheduler.schedule_hold`·`batch_hold`): 깨진 규칙 → paused / 유지보수·drain → skipped /
    지연 > 유예 → missed / 사이트 특권 실행 꺼짐 → skipped / 켠 사람(armed_by) 자격 → paused(`privileged.privileged_admin_problem` — 자동 확인
    `auto_confirm_hold` 의 세 검사를 뽑아 둘이 같은 규칙을 쓴다) / 배치 JSON 읽기 실패 → paused `batch_schedule_batch_unreadable` / 배치 없음 →
    paused `batch_not_found` / 배치 요청자 자격·세션 인증 배치 → paused / 연산 밖·옵션 검증 실패 → paused / planner 가 자식을 결정적으로 거부할
    원인(스토리지 없음·꺼짐·denylist — 기존 코드 `storage_missing`·`storage_disabled`·`identity_denied`) → paused / 항목 0 → skipped / 지금 파괴적인데
    지문(연산·옵션·항목 집합·실행 신원·자동 확인·스토리지 경로 바인딩)이 무장 때와 다르거나 동의 없이 무장됨 → paused `batch_schedule_batch_changed`
    / 비종단 → skipped(Previewing·Running `batch_schedule_batch_running`, PreviewReady `batch_schedule_awaiting_confirm`, Cancelling
    `batch_schedule_batch_cancelling`) / 스토리지 준비 안 됨 → skipped `storage_not_ready` / fired. 그래서 진행 중 배치는 스토리지가 준비 안 됐어도
    `batch_schedule_batch_running` 으로 기록된다.
  - 발사 = 한 트랜잭션: 항목 → 배치 잠금 → 잠근 행으로 재확인 → prev 스냅숏 → `_rescan_locked` → 스케줄 CAS(enabled·next_run_at·updated_at —
    같은 문장에서 두 사람의 계정 상태와 유지보수·drain 을 EXISTS 로 재확인) → 이력 + 감사 `batch/scheduled_rescan`(actor `system:batch-scheduler`).
    롤링 겹침으로 컨트롤러가 둘이어도 같은 슬롯은 한쪽만 쏜다. sync·rm 은 Previewing 으로만 간다.
  - 컨트롤러 루프 `batch-scheduler`(batch-orchestrator 바로 앞 — 같은 틱에 오케스트레이터가 집는다). 행마다 격리하고, 예외 행은 60초 백오프로
    due 질의에서 SQL 제외(독 행 20개가 정상 행을 굶기지 않게) — 유예 안이면 슬롯을 유지하고 넘으면 missed, 오류 이벤트는 슬롯당 1회. 깨진 JSON 은
    예외가 아니라 paused `batch_schedule_batch_unreadable`.
  - API `routes_batch_schedules`(`/api/admin/batch-schedules` — `/api/admin/batches/{id}` 와의 등록 순서 함정 회피): 조회·`:preview`·`{id}/runs`
    (기본 50·상한 200) = `require_admin`. 무장(POST·PATCH·`:enable`) = `require_admin` + 유지보수 거부 + `_require_batch_privilege`(공유 토큰 403 —
    모든 노드 에이전트가 주기적 root 실행을 심지 못하게), 파괴적이면 StrictBool `acknowledge_irreversible: true`(없으면 422
    `batch_schedule_ack_required`). `:disable` = `require_admin`(실행을 줄이기만 하는 브레이크 — 유지보수 중에도 받고 멱등). DELETE =
    **`require_session_admin`**(회차 이력 = 증거를 지우므로 배치 기록 삭제와 같은 경계 — dms-ssc(구현 시점 WIP·push 된 d198 모두)의 `require_admin` 보다 엄격). 응답 행에
    `destructive`·`acknowledged_irreversible`·`child_count`·`child_count_capped`·`child_count_limit`(= 2000 — 포탈에 하드코딩하지 않음)·`confirm_wait`
    (자동 확인 꺼진 sync·rm 의 고른 주기면 `{period_seconds, preview_ttl_seconds, unconfirmed_every_seconds}` — 미리보기 만료 24시간과 겹쳐 실제로는
    격일이 될 수 있다는 경고 재료).
  - **취소 + 스케줄 끄기 = `:cancel` 본문 `{disable_schedule: true}` 한 요청·한 트랜잭션**(취소 CAS 가 이긴 직후 같은 트랜잭션에서 끄고 응답에
    `schedule_disabled`, 감사 after.via = batch_cancel; CAS 가 지면 끄지 않는다). 본문이 없으면 그대로다(Cancelled 도 종단이라 다음 회차가 돈다 —
    사용자 결정). 설계의 「취소 뒤 포탈이 `:disable` 을 따로 부른다」는 원자성이 없어 설계 리뷰에서 바꿨다.
  - 배치 목록 행·상세 summary 에 `schedule` 요약(view 없음·all 은 모양 그대로 — dms-ssc 와 같은 자리), 작업 목록의 배치 요약에 `batch_schedule`.
  - 포탈: 작업 메뉴 「배치 스케줄러」(`/admin/batch-schedules`, 아이콘 CalendarClock — 번들 lucide-react). 목록(배치·작업·대상 스토리지·주기·다음
    실행(KST)·마지막 결과·사용·켠 사람·관리, 15초 폴링, 자식 80% 경고·확인 대기 경고·자동 일시정지 사유, 회차 기록 창), 추가·수정 창(프리셋 4종,
    `:preview` 300ms 디바운스 「다음 5회」, 파괴적이면 동의 체크 전 저장 비활성, 자동 확인 배치는 「자동 확인을 켠 사람 X · 스케줄을 켠 사람 Y」).
    배치 상세: 머리 배지(켜짐 = 규칙·다음 실행 KST, 일시정지 = 사유, 꺼짐) + 「스케줄 추가」(같은 창, 배치 고정), 취소 창 「이 배치의 스케줄도 끄기」
    (켜진 스케줄이 있을 때만 창 — 기본 체크, 없으면 예전처럼 바로 취소), 삭제 창 「스케줄과 회차 기록도 함께 삭제됩니다」(배치 상세·작업 목록의
    배치 단위 삭제 두 창 — 문구 한 벌 `scheduleDeleteNotice`).
  - 사유 코드 16개 양쪽 등록(`reasonCodes.json` + `api.ts` REASON_MESSAGES — 설계 15 + `batch_schedule_batch_unreadable`): `invalid_batch_schedule`·
    `batch_schedule_not_found`·`batch_schedule_exists`·`batch_schedule_ack_required`·`batch_schedule_unsupported_operation`·`batch_schedule_drain`·
    `batch_schedule_missed`·`batch_schedule_privileged_disabled`·`batch_schedule_owner_not_privileged`·`batch_schedule_requester_not_privileged`·
    `batch_schedule_empty`·`batch_schedule_batch_changed`·`batch_schedule_batch_running`·`batch_schedule_awaiting_confirm`·
    `batch_schedule_batch_cancelling`·`batch_schedule_batch_unreadable`. 요청자 문구는 k8s 뜻(「…지금 root 실행 자격이 없거나 비활성이거나,
    배치가 로그인 세션으로 만들어지지 않아 회차의 항목이 모두 거부됩니다 — 스케줄로 돌릴 수 없어 멈췄습니다…」 — dms-ssc 의 「비 root 강등」이
    아니다).
- **커밋**: a5cc398f(코드 — `feat(portal)`, 82파일 +8,917/−946), 2efdb32d(testbed-k8s 오버레이 d27 → d28), 「문서」(이 항목, ARCHITECTURE §5·§7·
  불변식 「배치를 가리키는 새 테이블은 배치 삭제 목록에도」, BACKLOG, CLAUDE.md, deploy/README)와 e2e 18 수정(아래).
- **검증**: 백엔드 전체 5884 passed / 14 skipped, vitest 2096 passed(122 파일), tsc -b 0, build `dist/index.html` 외부 URL 0(CSS `url(http…)` 0),
  e2e 38 passed(18 spec — 새 `18-batch-scheduler` 3건, `03-layout` 2건(배치 목록 열·상세 찾기 줄·쪽 넘김, 스케줄러 목록·상세 배지 — 1280·375),
  `15-usage-filters` 1건(합성 250행 쪽 넘김·390 시트 차트)).
  - 리뷰 3관점 지적 5건 반영(①~③ 은 옛 코드에서 새 vitest 가 빨간불인 것을 확인, ⑤ 는 vitest 추가, ④ 는 삭제): ① 「현재 항목 CSV」 창을 다시 열면 지난번 전 항목 목록이 새로 받는 동안
    잠깐 보여, 바로 복사해 「CSV 로 전체 교체」하면 그 사이 바꾼 항목이 조용히 빠진다 → 받는 중에는 textarea 를 그리지 않음 ② 목록 밖 경로
    시트의 「최신 scan」을 리포트 생성 시각 순(newestPoint)으로 골라 서버 `latest_job_id`(성공 기록 순)와 갈릴 수 있었다 → `latest_job_id` 로
    고름 ③ 배치 상세 `?page=` 에 상한이 없어 큰 값이면 offset 이 「1e+21」로 직렬화돼 422 로 굳었다 → 10,000,000 상한(사용량과 같은 값)
    ④ 안 쓰게 된 `SizeDistribution` 삭제(두 번째 크기 분포 구현이 다시 쓰이지 않게) ⑤ 작업 목록의 배치 단위 삭제 창에 스케줄 캐스케이드 안내가
    없었다 → 추가.
  - 구현 중 잡은 것: 배치 상세 검색어가 지워지는 경합(「새 q + 옛 page」 응답의 쪽 당김이 같은 틱의 주소 쓰기를 덮음 — react-router 함수형
    `setSearchParams` 같은 틱 경합, vitest 로 재현 → 쿼리는 주소 값으로만). 첫 게이트의 e2e 15 실패는 테스트 전제 오류였다(하네스 가짜 dscan
    리포트는 읽히지 않는다 → 그 scan 의 포인트를 읽힌 리포트 모양으로 합성).
  - 실증 뒤 수정 — e2e `18-batch-scheduler` 의 시각 의존 불안정: 스케줄 시각이 고정(매주 월·수 03:30, rm 창 기본 매일 02:00, 6시간마다 00:30
    기준)이라 돌리는 시각에 따라 테스트 중에 회차가 돌 수 있었다(그러면 「아직 없음」·기록 창 단언이 깨지고 끝난 배치가 실제로 전체 재실행된다)
    → 모든 생성을 `farSlot()`(지금 KST 의 시 + 12 — 03-layout 과 같은 규칙, 다음 슬롯 최소 11시간 뒤)로, 하루 슬롯이 여럿인 interval 테스트는
    「매월 31일」(요약이 가장 긴 꼴 — 넘침 검사에 더 맞다)로 바꾸고 머리 주석의 틀린 문장을 고쳤다. 테스트 파일만(앱·이미지 무관) — 그 spec
    3 passed, `tsc -b`·`tsc -p tsconfig.e2e.json` 통과, `batchSchedule.test.ts` 8 passed.
- **합성 성능**(로컬, 워크트리 코드):
  - #66(`tests/perf_usage_paging.py` — pytest 수집 밖, 다시 잴 때 같은 조건): 한 스토리지 1만 대상 + 성공 scan 6.5만 잡 + 다른 스토리지 2천
    대상(합 6.9만 잡), 요약 캐시 데움(~3.6 KiB/행), 케이스마다 새 프로세스·2회. sqlite — last_scan 1·50·100쪽 0.30~0.34 s(파이썬 힙 최대 2.9~3.0 MiB,
    응답 362~368 KiB), 범위 밖 101쪽 0.46~0.47 s(재질의 포함); bytes 1·50·100쪽 1.27~1.34 s(힙 최대 24.1 MiB, RSS +20 MiB), bytes 101쪽 0.28~0.29 s
    (지표 계산 생략). PG16(로컬, fsync off) — last_scan 0.20~0.23 s, 101쪽 0.27~0.28 s, bytes 1.24~1.30 s(힙 24.0 MiB), bytes 101쪽 0.18 s. 뒤 쪽도
    1쪽과 같은 급이다(설계 기대치 0.25 s / 1.3~1.4 s 와 일치). 범위 밖 재질의 비용은 +0.15 s(sqlite)·+0.05 s(PG).
  - #67(TestClient + sqlite, 항목 60% 에 자식·잡·v2 미리보기 요약, 중앙값): scan N=2000 옛 전체 70 ms·1.06 MB → 요약 2.5 ms·805 B, 항목 1쪽 12 ms·
    61 KB, 마지막 쪽 10 ms·40 KB, 상태 필터 10 ms, q 15.6 ms(PV 이름 판정 포함 18.6 ms), payloads 25 ms·214 KB. scan N=5000 옛 193 ms·2.64 MB ↔ 요약
    2.1 ms + 쪽 19.5 ms·61 KB. sync N=2000 옛 81 ms·1.22 MB ↔ 3.6 ms + 12.4 ms·70 KB, N=5000 옛 207 ms·3.06 MB ↔ 6 ms + 21 ms·70 KB. 2.5초 폴링 한 번의
    전송량이 1.2~3 MB 에서 62~71 KB 로 15~40배 줄었고, 쪽 시간은 N 이 커져도 거의 늘지 않는다. 렌더(jsdom + React Profiler): 그리는 행은 N 과 무관하게
    100개, N=2000 첫 로드 commit 20~25 ms·폴링 1회 2~3.4 ms·체크박스 하나 약 2 ms. (조사 때 옛 화면 실브라우저 4× 스로틀: N=800 로드 215 ms·폴링
    121 ms, N=2000 로드 527 ms·폴링 ~290 ms + 잦은 긴 작업 — 환경이 달라 직접 비교하지 않는다.)
- **배포**: 포탈 빌드 d28(소스 a5cc398f, 3종 dms-mpifileutils·dms·dms-agent, 210초) → 릴리스 dms-agent → dms-api → dms-controller → job-image 모두
  Applied(live == 동봉 d28), 오버레이 bump 2efdb32d. 새 테이블은 기동 때 `CREATE … IF NOT EXISTS`, 새 설정 키 없음.
- **라이브 실증**(d28, dmsk8s, 관리자 mason 세션 API·실 Chrome, kubectl 은 `--context dmsk8s` 읽기만 — 원자료 job tmp `portal3/live/`, 절은
  `portal3/PROOF-d28.md` L1~L3):
  - **L1 스케줄러**(15:37~16:02 KST):
    - 배포 확인: `GET /api/admin/batch-schedules` 200 `[]`, 테이블 2·인덱스 4(UNIQUE 포함, psql 읽기), 리스 `loop:batch-scheduler` 갱신 중(30초 리스 —
      오케스트레이터와 같은 holder), 컨트롤러 로그 40분 0줄(정상 틱은 로그를 남기지 않는다 = 루프 실패 0), 배치 목록 행에 스토리지 세 키·`schedule`.
    - scan 회차: 배치 e5fe(cephfs-dms, 2항목, Completed ok1/fail1)에 매일 15:41 KST → 슬롯 06:41:00Z 에 06:41:01Z **fired**(지연 1초), requeued 2,
      prev Completed/2/1/1 → Running → 06:41:55Z Completed ok2/fail0(발사 → 종단 약 54초), `next_run_at` = 다음날 같은 시각. 감사 806
      `batch/scheduled_rescan`, actor `system:batch-scheduler`(after 에 schedule_id·armed_by·scheduled_for·requeued·지문·next_run_at). 사람의 생성·
      수정·끄기도 감사 행(802·807·808·809).
    - 건너뜀(실행 중): 스캔 한 회차가 약 54초라 분 단위 슬롯과 겹치게 하려고 슬롯 28초 전에 사람 `:rescan` → 06:45:04Z **skipped
      `batch_schedule_batch_running`**, requeued null(0 아님), 이벤트 없음(설계대로 이력 행만). `:disable` 뒤 이력 2건 보존.
    - sync 확인 게이트: 배치 9c36 회차 fired(지연 4초) → **Previewing**(Running 직행 없음). 다만 소스 `team-a/datasets/proof-*` 가 테스트베드에서
      지워져 프리플라이트 `source_not_readable` 로 Rejected 됐다(테스트 데이터 탓 — 스케줄러 결함 아님). 새 sync 배치 2d25(k8s-pv-nfs
      `team-a/datasets/sample` → `team-b/scratch/l1-sched-proof-d28`, 자동 확인 꺼짐)로 재현: 06:59:00Z fired(지연 0초) → Previewing → 06:59:27Z
      **PreviewReady 에서 멈춤**(확인 0회) → 다음 슬롯 07:01:03Z **skipped `batch_schedule_awaiting_confirm`**(batch_status PreviewReady).
    - 취소 + 끄기: `:cancel {disable_schedule: true}` → 200 `{status: Cancelled, schedule_disabled: "717ee685…"}` 한 응답(0.148 s), 상세·목록의
      schedule enabled false·next null, 감사 817 after.via = batch_cancel. 세션 DELETE 200 → runs 404, 감사 818(before.run_count 2). 목적지는 끝까지
      만들어지지 않았다.
    - rm 동의 게이트(배치 2c0a): 동의 없음·`false` → 422 `batch_schedule_ack_required`, 문자열 `"true"` → 422 bool_type(StrictBool), `true` + 매월
      1일 03:00 → 201(destructive true, next 2026-11-30T18:00:00Z = 12-01 03:00 KST) → 같은 초에 끔 → 회차 0건 → 세션 삭제. 감사 803(지문)·804·805.
    - 공유 토큰(Bearer — 시크릿을 메모리로만 읽음): 생성·`:enable` 403 `privileged_not_authorized`, DELETE 403 `admin_session_required`, `:disable`
      (이미 꺼짐) 200 멱등(감사 행 없음), runs 200.
    - KST·경고(API 필드): 매일 15:41 → 06:41Z, 매월 31일 → … 2027-02-28(말일 당김), 매주 [4,1] → 월·목으로 정렬, 규칙 오류 5종(요일 0개·hour true·
      every_hours 5·여분 키·kind cron) 모두 422 `invalid_batch_schedule`. `confirm_wait`: 자동 확인 꺼진 sync 매일 {86400, 86400, 172800}·6시간
      {21600, 86400, 108000}, rm 매일 {86400, 86400, 172800}, 매주·scan 은 null. 이벤트 `batch_schedule_fired` 3건, error·missed·paused 0건. 이력 기록
      지연 0~4초(루프 5초 안).
  - **L2 큰 배치·사용량 API**(16:07~16:25 KST):
    - 픽스처: cephfs-dms 아래 새 전용 디렉터리 `/cephfs/managed/dms-proof-d28`(p0001..p1500, 파일 4,784개, 175,716,256 B — 크기 0 B~4 MiB·mtime
      0.5~4,000일 전을 섞음). dms-agent 파드는 호스트 `/` 를 읽기 전용으로만 마운트해 쓸 수 없어, k8s-w1 SSH sudo 로 생성 스크립트를 stdin 으로
      실행했다(노드에 파일 안 남김).
    - 큰 배치 78b7: scan 1,500 항목·max_concurrency 6 → 202, 0.201 s(본문 90,440 B). 잡 모양은 정책 기본(dscan node_count 2·procs_per_node 1).
      첫 성공 27 s, 101번째 656 s, 131번째 842 s(전체 9.33건/분, 정상 구간 9.57건/분, 성공 간격 중앙값 5 s). 사용량 대상이 131개가 된 16:22:57 KST 에
      `:cancel` 200, 0.761 s → Succeeded 131·Cancelled 1,369(실행 중이던 6건은 요청·잡 Cancelled). 진행 중 폴링(n=43): summary 중앙값 10 ms(최대
      43 ms), `/targets limit=1&q=` 21 ms(최대 51 ms).
    - view 별(취소 뒤 1,500 항목, 3회 중앙값, 비압축 — 서버는 `Accept-Encoding: gzip` 에도 압축하지 않는다): view 없음 625,536 B·58.2 ms,
      all 625,536 B·56.9 ms, **summary 1,044 B·6.2 ms**, **items 100 45,772 B·14.5 ms**(offset 1400 41,385 B·14.3 ms), items 500 212,388 B·30.1 ms,
      payloads 148,901 B·21.9 ms. 폴링 한 주기가 625,536 B → 46,816 B(summary + items 1쪽, **13.4배 작음**). 진행 중(Queued 1,490)도 같은 급.
    - 검색·필터·범위: `p0123` → 1, `P0123` → 1, `p01` → 100, `p1` → 501, `p0123 p0124` → 0(AND), `p01→p0123` → 1, 9토큰은 9번째 무시, 스토리지
      이름 → 0, sync 2d25 는 출발·도착 AND(`sample l1-sched` → 1, `sample zzz` → 0). 상태 Succeeded 131·Cancelled 1,369, 모르는 값 200 빈 결과, q +
      상태 `p01`&Succeeded 32·&Cancelled 68. offset 1500·99999 → 행 0·total 유지, offset −1·q 513자·`view=bogus` → 422, limit 0 → 1·9999 → 500 으로
      접음, 없는 배치 404 `batch_not_found`. 목록 `?limit=101` 9행 5,759 B·6.0 ms, 원문 payload 없음.
    - #66 쪽 넘김: cephfs-dms 대상 134개(e2e 3 + 픽스처 131) limit 100 → 100행(has_more true, pageable 134)·34행(false)·범위 밖 0행(pageable·
      matched 134 유지). last_scan·scans·bytes·files·hot × asc·desc **10가지 모두** 쪽(100·50) 이어 붙임 == offset 없는 전체 순서(중복·누락 0),
      `q=dms-proof-d28`(131)도 같음. k8s-pv-nfs(5 대상) limit 2·offset 2/4/6 과 PV 갈래(PV 이름 검색·PV 축 필터)도 10가지 모두 같음. 422: limit 201·0,
      offset −1·1000000001. 시간: limit 200 전체 66~69 ms(SQL)·81~88 ms(지표), 쪽 1쪽 51~54·66~74 ms, 2쪽 23~42 ms.
    - 차트 동일성: 픽스처 131 경로 **전수**에서 `/targets` 행 `latest` == 배치 항목 요청의 `scan-stats`(summary·크기·온도 3축·total_bytes·
      generated_at) 131/131, `scan-history.latest_job_id` == `latest.job_id` 14/14(10번째마다), 성공 scan 없는 p1499 → null·points 0.
  - **L3 UI**(실 Chrome headless, 1440·390, 16:33~16:49 KST): 화면 묶음 10개에서 콘솔 오류·실패 요청·가로 넘침·이상 글자 0(스크린샷 56장).
    - 사용량: 「1–100 / 전체 134개」, Pager 「1 / 2쪽」 한 줄(390 높이 34 px), 다음 → `?page=2` 34행 「101–134 / 전체 134개」(새로고침 유지, 뒤로
      가기 = 1쪽), 딥링크 `?page=3` → page=2 로 replace, 정렬·검색은 page 를 지움(「필터에 맞는 131개 중 101–131」), k8s-pv-nfs 는 「5개」·Pager
      없음. 시트 쪽 경계: 1쪽 끝 p0032 「목록 100/134번째」 → 다음 → `page=2&target=…/p0031`(「101/134번째」) → 이전으로 복귀. 시트 「최신 scan」
      (p0123)의 막대 `data-tip`·누적 % 가 배치 78b7 항목 122 펼침과 **문자열까지 같다**(1440·390), 스토리지 합계 「파일 크기 분포」도 같은 차트.
    - 배치 목록: 사이드바 단일 작업 → 배치 작업 → 배치 스케줄러 → 전체 작업, 머리줄 「작업」 다음 「대상 스토리지」(cephfs-dms / k8s-pv-nfs →
      k8s-pv-nfs / k8s-pv-nfs), 390 은 표 래퍼 안 가로 스크롤(문서 넘침 0).
    - 큰 배치 상세 성능(CDP CPU 4× 스로틀, 긴 작업 > 50 ms): 첫 로드(내비게이션 시작 → 쪽 전체 행 표시, 3회 중앙값) 78b7(1,500 항목, 100행)
      401 ms(390)·426 ms(1440), 긴 작업 3~4개(최대 68~100 ms), JS 힙 GC 후 4.4~4.5 MB ↔ 작은 배치 e5fe(2 항목) 368·390 ms, 긴 작업 2개 — 큰 배치의
      추가 비용 +33~36 ms. 첫 로드의 상세 API 는 summary 1,344 B + items 46,072 B 둘뿐(`view=all` 0). 상호작용(390/1440): 폴링 1주기 재현 38/31 ms·
      긴 작업 0, 쪽 1→2 195/179 ms, 마지막 쪽(15쪽) 129/110 ms, 검색 `p0123`(디바운스 300 ms 포함) 402/402 ms, `p1`(「1 / 6쪽」) 417/442 ms, 상태 칩
      127/125 ms, 항목 펼침(차트 2개) 152/138 ms(긴 작업 1개 51 ms). 실제 2.5초 폴링은 종단 배치라 멈춰 있어(5초 동안 요청 0 — 설계대로)
      `page.route` 로 브라우저 안 응답만 고쳐(summary Running, 매 주기 100행 상태가 전부 바뀜 — 서버 무변경) 재현했다: 25초에 10주기, **긴 작업 0·
      50 ms 넘는 프레임 0**(390·1440), 폴링 중 검색칸 입력 171~174 ms.
    - 스케줄러 UI: L1 스케줄 행(「매일 15:45 (KST) · 건너뜀 · …」)·회차 기록 창 2행, 배치 상세 배지 「스케줄 꺼짐 — 매일 15:45 (KST)」 → 링크가 수정
      창을 연다. 추가 창 프리셋 4종의 서버 「다음 5회」, rm 을 고르면 반복 삭제 경고 + 동의 체크 전 「추가」 disabled, 확인 대기 경고(6시간 →
      「30시간에 한 번」, 매일 → 「2일에 한 번」). UI 로 L1 스케줄 2단 삭제 → 빈 목록 → e5fe 상세 「스케줄 추가」(매월 1일 03:00) → 배지 「스케줄 매월
      1일 03:00 (KST) · 다음 2026-12-01 03:00」 → 전체 재실행 → 취소 창 「이 배치의 스케줄도 끄기」 기본 체크·주 버튼 「취소하고 스케줄 끄기」(닫음 —
      `:cancel` 0건) → 삭제 창 스케줄 경고 → 스케줄 UI 삭제. 감사 819~821.
    - 확인 창: 2d25 `:rescan` → PreviewReady(회차 3, 30.3초) → 「배치 확인」 창은 열 때마다 `view=all` 1회(닫은 뒤 추가 0), 그 회차 3 == 폴링 summary,
      「실행할 항목 1 / 전체 1」 — 「실행 확인」은 누르지 않고 닫음(confirm 0건). UI 취소는 스케줄 없는 배치라 창 없이 바로 `:cancel`(본문 없음) →
      Cancelled. 목적지 없음 확인.
  - 실증에서 못 본 것: missed(설계 §3.9-4 — d28 롤아웃 때 스케줄 0건), 80% 경고가 켜진 상태(child_count 최대 6 — 필드만 확인, 화면 조건은
    vitest), PV 창 넘침 `pageable_count: null`·`_with_path_hits`(대상 1만 초과 필요 — 단위 테스트), 「PV 이름에만 맞는」 검색(테스트베드 PVC 이름이
    모두 경로에 들어 있다).
  - 관측(결함 아님): 배치 상세의 `schedule` 은 summary·목록에만 있고 view 없는 전체 상세에는 없다(설계 문구와 다르지만 포탈 머리는 summary 를
    쓴다), 서버 응답 비압축(`view=all` 625 KB — BACKLOG 「API 응답 압축(GZip) 검토」의 근거), 390 폭 차트 축 글자 말줄임(전체 구간은 툴팁),
    확인 창은 회차 숫자를 글자로 보이지 않는다(변경 전과 같다), 사람의 「전체 재실행」은 감사 행이 없다(BACKLOG).
- **실증 결함**: 0건(L1·L2·L3). 라이브에서 고칠 것이 없어 d28 을 다시 빌드하지 않는다 — 실증 뒤 수정은 위 e2e 하나뿐이고 테스트 파일이라
  다음 빌드(d29) 이미지와도 무관하다.
- **테스트베드에 남긴 것**(사용자 열람·다음 UI 실증용 — 지우지 않음. 픽스처·78b7·2d25 는 **11-16 정리됨(d29 항목)**):
  - 픽스처 `/cephfs/managed/dms-proof-d28`(p0001..p1500, 파일 4,784개, 175,716,256 B, root:root — CephFS 라 k8s-w1·w2 모두에서 보인다). 전용
    디렉터리 밖은 만들거나 바꾸지 않았다.
  - 큰 배치 `78b727282fc04234bceacbf3d67d8d93`(「d28 실증 L2 — 큰 scan 배치 1,500 경로」, Cancelled, Succeeded 131(p0001–p0131)·Cancelled 1,369, 자식
    요청 137 = scan Succeeded 131 + Cancelled 6, 100개씩 15쪽). 작업 목록의 배치 단위 삭제(자식 포함 — `request_purges.delete_batch`)로 지우면 자식
    요청·data_jobs 가 함께 지워져 cephfs-dms 사용량 대상이 134 → 3개로 돌아간다(쪽 넘김을 보일 수 없게 된다 — 픽스처 디렉터리는 남는다). 배치 상세의
    「배치 삭제」(기록만 — `BatchesRepository.delete`, 자식 보존)는 사용량에 영향이 없다.
  - sync 배치 `2d25eb3e1b8b439a8be30c2a304e4bac`(「L1-d28-sched-proof」, 자동 확인 꺼짐, Cancelled, 확인 0회라 목적지 없음) — 소스가 있는 sync
    배치라 확인 대기 UI(배지·「스케줄도 끄기」·확인 창)를 다시 보일 때 쓴다(PreviewReady 까지 약 20~30초). 기존 sync 배치 5개(9c36·e7a1·1a19·90a1·
    379d)와 rm 배치 2c0a 의 대상 `team-a/datasets/proof-*` 는 지금 디스크에 없어 재실행·회차가 `source_not_readable` 로 Rejected 된다.
  - 스케줄은 0건이다(L1 이 남긴 8e4155e7 은 L3 가 UI 로 지웠다). 오늘 생긴 자식 요청(지우지 않음): e5fe scan +6(회차 2·사람 전체 재실행 4) →
    Completed ok2/fail0, 9c36 sync +1(Rejected), 2d25 sync +3(Cancelled). 감사 행 802~821(append-only). scan 잡의 dscan 리포트·Completed vcjob 은
    DMS 보존 정리 몫. 클러스터 리소스·sync 쌍·계정·스토리지 설정·current-context(dms)는 바꾸지 않았다.
- **남은 일**: BACKLOG 「포탈 개선·배치 스케줄러 후속 — 2026-11-16」 — 스케줄 회차가 쌓는 자식 요청의 정리 정책(**사용자 확인 필요** — 지금은
  자동 정리 없음 + 80% 경고), 사람의 `:rescan` 감사 행, 배치 목록에 스케줄 요약 표시, 배치 항목 일괄 삭제 컬렉션 엔드포인트, 배치 상세 view
  없음(옛 탭 호환)의 정리 시점, API 응답 압축(GZip) 검토, dms-ssc 역이식 후보(Pager null 모드·`/targets` 의 `has_more`·`pageable_count`·`filters` 메아리·PV
  이름 검색·스케줄 삭제 = session admin·`:cancel disable_schedule`·회차 백오프·사전 판정의 꺼짐·denylist·`confirm_wait`), dms-ssc 에서 골라 올
  스토리지 사용 중 가드(활성 배치·생성 — dms-ssc 14cb6741·8516568d), vite 빌드 청크 경고.

### ✅ 입구 — Envoy Gateway 폐기, 클러스터의 Traefik 하나로 교체(테스트베드 영구 적용·실증) d27 — **완료·실증**(2026-11-16)

사용자 결정(2026-11-16) 「envoy 사용방식은 폐기하고 traefik을 사용하도록」 — 병행 모드 없이 교체한다. 설계는 세션 job tmp `traefik/DESIGN.md`
(D1~D11), 근거는 Traefik v3.7.4 소스·바이너리 실측(같은 폴더 `understand.json`). 옛 ⑥(11-03)·테스트베드 컷오버(11-04)·Gateway API 하한(11-13)·
리스너 포트/off(11-14) 항목은 당시 기준으로 둔다.

- **배경**: OA 프로덕션(Rancher/RKE2)에는 rke2-traefik 이 기본으로 깔려 있다 — v3.7.4 DaemonSet, 모든 노드 hostPort 80/443(web :8000·websecure
  :8443, websecure `http.tls=true`), providers kubernetesingress·kubernetescrd(ingressClass traefik), IngressClass `traefik`(controller
  `traefik.io/ingress-controller`). Envoy 입구(11-03 ⑥·11-14)는 그 옆에 DMS 가 따로 세운 두 번째 입구였다. 이번에 확인한 OA 의 Avi L4 VS(VIP:443 →
  노드 :443)는 **PROXY v2 를 켰다**(11-14 항목의 「미지원」 보고와 다르다). Traefik 정적 설정(PROXY trustedIPs·타임아웃)은 사이트가 Rancher
  chartValues·HelmChartConfig(`kube-system/rke2-traefik`)로만 바꾼다 — 그래서 **DMS 는 Traefik 을 설치·변경하지 않고 Ingress 하나만 만든다**.
- **Traefik 동작(설계 근거)**: PROXY — trustedIPs 출발지는 헤더가 있으면 쓰고 없어도 받는다(USE), 비신뢰 출발지의 헤더는 읽어서 버리고 실제
  피어를 쓴다(IGNORE — 위조 불가, 대신 「PROXY 필수」 강제는 불가능). `forwardedHeaders.trustedIPs` 가 비면 클라이언트가 보낸 X-Forwarded-*·
  X-Real-Ip(밑줄 변형까지)를 지우고 원 IP 를 XFF 끝에 붙인다 → 앱 `auth.client_ip`(XFF 마지막 항목) 규칙 그대로(앱 코드 무변경 — docstring 만).
  readTimeout 60s 는 요청 본문 읽기에만, writeTimeout·responseHeaderTimeout 0 → 긴 다운로드 OK. 상류 idleConnTimeout 90s > uvicorn 기본
  keep-alive 5s → 본문 있는 POST 가 막 닫힌 재사용 연결에 실리면 502. Buffering 미들웨어는 응답 버퍼를 끌 수 없다 → 본문 상한은 앱에서.
  `router.entrypoints` 가 없으면 :80 평문에도 붙고, 모르는 SNI·IP 는 TRAEFIK DEFAULT CERT + 404. Middleware·ServersTransport 에는 ingressClass
  필터가 걸리지 않는다(IngressRoute 만) — 그래서 둘 다 쓰지 않는다.
- **Ingress 하나**(새 플랫폼 층 `deploy/platform/ingress`): networking.k8s.io/v1 Ingress `dms/dms-portal-traefik` — base 앱 46-ingress 의
  `dms-portal`(class nginx — testbed-k8s 되돌림 경로, prod 는 오버레이가 지운다)과 이름이 달라 한 클러스터에 함께 있어도 서로 덮지 않는다.
  `ingressClassName` = `INGRESS_CLASS`(기본 traefik), 어노테이션 `traefik.ingress.kubernetes.io/router.entrypoints` = `TRAEFIK_ENTRYPOINT`(기본
  websecure — :80 평문에 안 붙게)·`router.tls: "true"`, host·tls hosts = `PORTAL_DOMAIN`(**템플릿·고정 오버레이 모두 필수** — 소문자 호스트 이름
  문법, 와일드카드·포트·IP 금지; host 없는 catch-all 은 공용 Traefik 에서 남의 미매칭 호스트를 가로채므로 두지 않는다), secretName =
  `PORTAL_TLS_SECRET`(기본 dms-portal-tls), `/` Prefix → `dms-api:http`. Middleware·ServersTransport·IngressRoute 없음(kubernetescrd 의존 없음).
  render.env 입구 키 `INGRESS_LAYERS="platform/ingress"`·`INGRESS_CLASS`·`TRAEFIK_ENTRYPOINT`·`PORTAL_TLS_SECRET`·`PORTAL_DOMAIN`·`PORTAL_VIP`,
  stdout 은 `GATEWAY:` 세 줄 대신 `INGRESS: <OUT>/platform/ingress` 한 줄. 옛 `GATEWAY_PROXY_PROTOCOL`·`GATEWAY_LISTEN_PORT`·
  `GATEWAY_ENVOY_CPU_REQUEST`·`GATEWAY_ENVOY_MEMORY_REQUEST` 가 values.env 에 남아 있으면 실패하지 않고 stderr 에 알림 한 줄
  `note: values.env 의 … 는 더 쓰지 않는다(2026-11-16 — 입구는 클러스터의 Traefik) — 무시함(…)`(옛 LDAP 키 알림과 같은 방식).
- **앱 — Envoy BackendTrafficPolicy 가 하던 두 가지**:
  - 요청 본문 상한(`api/body_limit.py` 순수 ASGI 미들웨어 — 인증·라우팅보다 앞): `DMS_MAX_REQUEST_BODY_BYTES`(기본 20971520 = 20MiB = 옛 입구
    값, 하한 1MiB — 아래면 기동 거부). Content-Length 가 넘으면 앱을 부르지 않고 413, 길이 머리가 없거나(chunked) 스트림이면 받은 바이트를 세어
    넘는 순간 413 — 응답 `{"detail": "request_too_large"}`(사유 코드 `reasonCodes.json`·`REASON_MESSAGES` 양쪽 등록, 다운로드 **응답** 상한
    413 `artifact_too_large` 와 다른 코드). Envoy 때는 입구 경유만 걸렸지만 이제 **dms-api 에 닿는 모든 요청**(클러스터 안 에이전트 포함)에
    걸린다 — 스스로 더 큰 상한을 재는 k8s 동기화 결과 POST(`/api/agent/k8s-sync/runs/{run_id}` — `k8s_sync_max_bytes` + 1MiB, 넘으면 413
    `k8s_sync_too_large`)만 뺀다. 413 에 Connection: close 를 붙이지 않는다(안 읽은 본문 때문에 RST 가 먼저 가면 프록시가 413 대신 502 를 낸다).
  - keep-alive: `DMS_API_KEEP_ALIVE_SECONDS`(기본 120, 하한 1) → uvicorn `timeout_keep_alive`. 입구의 상류 유휴 타임아웃(Traefik 기본
    idleConnTimeout 90s)보다 길어야 백엔드가 풀의 유휴 연결을 먼저 닫지 않는다 — Envoy 때(BTP connectionIdleTimeout 4s < 5s)와 같은 원칙을
    반대쪽에서 지킨다. 사이트가 idleConnTimeout 을 120s 이상으로 올리면 이 값도 그보다 크게. 두 키 모두 base `20-config.yaml` 에 적지 않는다
    (코드 기본이 계약 — 바꿀 사이트는 오버레이 patch-config 에 더하고 dms-api 재시작).
- **설치기**:
  - 감지(`install-detect.py`): 항목 `traefik`(S8, 남의 것) — IngressClass `<INGRESS_CLASS>` 의 controller 가 `traefik.io/ingress-controller` 이고
    Traefik 워크로드(아무 ns 의 DaemonSet·Deployment — 이미지 이름 마지막 조각에 traefik)가 Ready ≥ 1 이면 C(reuse), IngressClass 없음·controller
    다름 → X(「DMS 는 Traefik 을 설치하지 않는다」 — RKE2 는 rke2-traefik), 워크로드 없음·Ready 0 → U. 워크로드 args 로 판정:
    `--providers.kubernetesingress`·`--entryPoints.<EP>.address` 가 없으면 X, http.tls 없음·`proxyProtocol.trustedIPs`·forwardedHeaders·readTimeout 은 notes,
    trustedIPs 없음(LB 가 PROXY v2 를 붙이면 연결이 깨진다)·`proxyProtocol.insecure=true` 는 warning, args 밖(`--configFile` 등)이면 C!. 항목
    `ingress`(S8, DMS 층 — 없음 A, DMS 라벨 D=, 라벨 없는 같은 이름 P). 옛 항목 이름 `gateway-api`·`envoy-gateway`·`gateway` 는 사용법 오류로
    바뀐 이름을 알려 준다(install-detect·install.sh `--adopt`·`--upgrade-addon`).
  - S8 「입구(Traefik)」: 감지 traefik 이 stop 이면 사유 그대로 멈춤 → TLS 게이트 → SSA platform/ingress → `deploy/ingress-probe.sh`. dms-api
    Endpoints 가 비었으면(첫 설치 — 앱은 S11) `--tls-only`. 프로브 FAIL 은 멈추고, **UNKNOWN 은 WARN 후 계속**한다(설치 호스트 → 노드 :443 이
    방화벽으로 막혔을 수 있다 — S12 의 VIP 경유 readyz 가 다시 본다). preflight 「2. 입구 (클러스터의 Traefik — DMS 는 설치하지 않는다)」:
    IngressClass 없음·controller 다름 FAIL, Traefik 파드 Ready 수 INFO. verify 5절 「입구」: Ingress 존재·class, ingress-probe
    (`INGRESS_PROBE_TIMEOUT` 기본 5초, UNKNOWN 은 WARN). 6절 「포탈」: VIP 경유 readyz(그대로), http 80 판정 — 404 INFO(Traefik web 에 DMS 라우터
    없음 — 정상)·200 WARN(평문 노출 — `router.entrypoints` 확인)·응답 없음 INFO(옛 「80 불가 = 정상」 FAIL 대신). S13 체크리스트: LB VS-443
    풀 = Traefik 이 도는 노드 :443, 헬스 = TCP 443 또는 SNI·Host 를 `PORTAL_DOMAIN` 으로 한 HTTPS /readyz, LB 가 PROXY v2 를 붙이면 Traefik
    websecure 의 `proxyProtocol.trustedIPs` 에 LB 출발지(SE). 새 메시지가 가리키는 앵커는 `deploy/README §2g`.
  - `deploy/ingress-probe.sh`(새 — 옛 host-net-probe.sh 의 출력·종료 관례): `--context <ctx> --domain <fqdn> [--nodes ip,…] [--port 443]
    [--timeout 30] [--cacert <CA>] [--tls-only]`. 대상 = Traefik 파드의 hostIP(못 읽거나 없으면 노드 InternalIP 전부), 노드마다 `openssl s_client
    -servername` 인증서 대조(주체·SAN 에 FQDN — TRAEFIK DEFAULT CERT 면 FAIL)와 `curl --resolve` readyz(200 PASS · 404 FAIL — Traefik 이 Ingress 를
    안 집었다 · 연결 실패 000 UNKNOWN). `--timeout` 은 FAIL 노드만 다시 재는 총 시간(간격 env `PROBE_INTERVAL` 기본 2초, UNKNOWN 은 재지 않는다).
    종료 0 PASS · 1 FAIL · 4 UNKNOWN · 2 사용법·환경, 줄 `NODE <이름|ip> <PASS|FAIL|UNKNOWN> <근거>`·마지막 `RESULT:`. kubectl 은 늘 `--context`
    ·get 만. 설치 호스트는 PROXY 없이 붙지만 Traefik 은 비신뢰 출발지를 헤더 없이도 받는다 — 그래서 노드 직접 확인이 된다.
- **제거**: `deploy/addons/gateway-api`(v1.6.1)·`deploy/addons/envoy-gateway`(v1.9.3)·`deploy/platform/gateway`·`deploy/host-net-probe.sh`,
  versions.env 의 `GATEWAY_API_VERSION`·`ENVOY_GATEWAY_VERSION`·`ENVOY_GATEWAY_IMAGE`·`ENVOY_PROXY_IMAGE`, images.lock 의 gateway·envoy 두 줄,
  render.sh 의 `GATEWAY_*` 렌더·`GATEWAY_LAYERS`·prod patch-config 의 `REPLACE_LOGIN_ALARM_SOURCE_FAILURES` 자리(출발지 경보는 base 값 그대로 —
  LB 가 PROXY 를 못 붙이는 사이트는 patch-config 에 `DMS_LOGIN_ALARM_SOURCE_FAILURES: "0"` 을 더한다), install-detect 의 세 항목·Gateway API/EG
  상수·핀 요구, install.sh S8 의 EG 단계·`hostport_gate`·sysctl·마스터 LISTEN 프로브·guard 의 envoy-gateway, preflight 의 마스터 :443·
  :19000~19003·sysctl `90-dms-envoy`·hostPort 충돌, verify 의 Gateway Programmed·「80 불가 = 정상」 FAIL, guard-images.sh 의 EG 분기, PriorityClass
  `dms-addon-critical` description 의 EG 컨트롤러, `tests/test_gateway_contract.py`(→ `test_ingress_contract.py`). `CONTROL_PLANE_LABEL_VALUE`
  는 유지(레지스트리·PG·api/controller — EnvoyProxy 패치만 빠졌다). 코드 커밋 64파일 +2,746/−86,164.
- **커밋**: aa26bbce(코드 — `feat(ingress)!`), f415eb84(testbed-k8s 오버레이 d26 → d27), 「문서」(이 항목, deploy/README §2g 를 「입구(Traefik)」로
  — 옛 하위 절 번호 2g-1~2g-6 의 새 위치는 부록 Z, prod·testbed README, ARCHITECTURE, BACKLOG).
- **검증**: 백엔드 전체 5716 passed / 14 skipped, vitest 2000 passed(115 파일), tsc -b 0, e2e 32 passed, deploy 셸 11개 `sh -n`/`bash -n`·Python
  361개 py_compile 오류 0. 두 사이트 렌더·`kubectl kustomize` 9층 rc 0, Envoy·Gateway API 객체 0 — testbed-k8s 렌더의 platform/ingress = Ingress
  하나(websecure·router.tls·class traefik·host dms-k8s.local·dms-portal-tls, `kube-context` = dmsk8s).
- **배포**: 포탈 빌드 d27(소스 aa26bbce, 3종, 225초) → 릴리스 dms-agent → dms-api → dms-controller → job-image 모두 Applied(live == 동봉 d27),
  오버레이 bump f415eb84(guard exit 0). 앱 본문 상한·keep-alive 는 Envoy 입구에서도 무해해 입구 전환 전에 먼저 출하했다.
- **테스트베드 Traefik**(rke2-traefik 모양 — testbed IaC): `kube-system/traefik` DaemonSet, `docker.io/rancher/hardened-traefik:v3.7.4-build20260608`
  (프로덕션과 같은 판), hostPort 80→:8000(web)·443→:8443(websecure, http.tls), providers kubernetescrd(ingressClass traefik)·kubernetesingress,
  websecure `proxyProtocol.trustedIPs=10.10.10.1/32`(호스트 edge = Avi SE 흉내), forwardedHeaders 비움, IngressClass traefik(기본 클래스 아님),
  control-plane toleration(테스트베드는 cp1 만 taint — RKE2 마스터는 taint 가 없다). 설치 `make traefik-dmsk8s`(태그 고정 CRD v3.7.4 install-once
  + 템플릿 apply + 실행 이미지 assert, `--check` = dry-run=server). 실측: CRI-O 가 hostPort 를 직접 DNAT 한다(이 클러스터 Cilium 은 KPR=false·
  portmap 체이닝 없음인데도) — 노드 `ss` 에 :443 이 안 보이고 INPUT 방화벽은 그 트래픽을 보지 못한다(PREROUTING DNAT → FORWARD).
- **전환**(사용자 경로 readyz 2초 간격 — 단계마다 무중단): ① Traefik 을 w1·w2 에만(`-e traefik_tolerate_control_plane=false`) — hostPort 동작
  (TRAEFIK DEFAULT CERT 404) ② 새 설치기 `install.sh --only S8` — 감지 traefik C(reuse)·ingress A → SSA → ingress-probe w1·w2 PASS(readyz 200·DMS
  인증서) ③ 호스트에서 w1·w2:443 직접 PROXY — 신뢰 출발지(10.10.10.1) + clientip 192.0.2.7 → 앱 client_ip=192.0.2.7, 비신뢰 출발지
  (192.168.75.215) + clientip 6.6.6.6 → 192.168.75.215 ④ d27 릴리스 뒤 edge 풀 cp1 → cp1·w1·w2(`make host-edge`, `portal_edge: traefik`, 백엔드
  `dmsk8s_nodes`): **25/25 200** ⑤ toleration true(`make traefik-dmsk8s`) → cp1 인수 + w1·w2 파드 롤링: **90/90 200** ⑥ Envoy 정리(아래 런북):
  **60/60 200** ⑦ cp443 은퇴 — `make baseline ANSIBLE='ansible-playbook --limit k8s-cp1 --tags cp443'`(cp1 sysctl `90-dms-envoy.conf` 삭제·런타임
  1024, nft `dms_testbed_cp443`·유닛 제거; 첫 실행 make rc 2 → 재실행 rc 0·changed 0 으로 수렴, 원인 미확인), `make host-net`(설치본 주석만
  달랐다) → `make validate-dmsk8s` **PASS=211 FAIL=0**, `post-boot-check.sh` **PASS=101 FAIL=0**.
- **Envoy 정리 런북**(테스트베드에서 실행한 순서 — 프로덕션도 DMS 가 깐 것만): 1) DMS 입구 객체 — ns dms 의 HTTPRoute·Gateway·
  ClientTrafficPolicy·BackendTrafficPolicy·EnvoyProxy `dms-portal`, GatewayClass `dms`(EG 컨트롤러가 Envoy DaemonSet 을 거둔다) 2) EG mutating
  webhook `envoy-gateway-topology-injector.envoy-gateway-system`(컨트롤러보다 먼저 — 남으면 파드 생성에 걸릴 수 있다) 3) ns `envoy-gateway-system`·
  cluster RBAC(`eg-gateway-helm-*` + binding) 4) EG CRD 8개(`*.gateway.envoyproxy.io`) 5) Gateway API CRD 는 라벨
  `app.kubernetes.io/managed-by=dms-install` 인 것만(테스트베드 10개 — DMS 가 v1.6.1 을 깔았다) + VAP·binding `safe-upgrades.gateway.networking.k8s.io` — 프로덕션 RKE2 는
  rke2-traefik-crd(Helm 소유)가 v1.5.1 을 깔았으니 **지우지 않는다**(라벨로 구분) 6) PriorityClass `dms-addon-critical` 은 남긴다(CNPG 오퍼레이터가
  쓴다) 7) 노드 설정 은퇴 — 마스터 `/etc/sysctl.d/90-dms-envoy.conf` 삭제 + 런타임 1024. 마스터 :443 INPUT 방화벽(SE 대역만)은 Traefik
  hostPort 트래픽에 무력하다(FORWARD 경로) — 위조 방지는 Traefik trustedIPs 가 한다. 라이브 확인: ns `envoy-gateway-system` 없음, Gateway API·EG
  CRD 0.
- **실증**(전환 뒤 d27, 모든 노드 Traefik):
  - 경로: VIP-A `10.10.10.1:443`·LAN `192.168.75.215:8443`·Tailscale `100.65.195.106:8443` → 200(HTTP/2), 노드 셋 :443 직접(SNI dms-k8s.local) →
    200·DMS 인증서.
  - 원 IP(앱 보안 이벤트 `client_ip` / `peer`): LAN 경유(edge PROXY v2) → 192.168.75.215 / Traefik 파드 IP(옛 Envoy 의 마스터 호스트 주소 자리),
    신뢰 출발지 PROXY 192.0.2.7 → 192.0.2.7, 비신뢰 출발지 PROXY 6.6.6.6 → 192.168.75.215. 모든 요청에 실은 위조 X-Forwarded-For 7.7.7.7·
    X-Real-Ip 6.6.6.6 반영 0. edge 경유 연결에 클라이언트가 PROXY 헤더를 심으면 edge 의 진짜 헤더가 먼저 가고 심은 바이트는 TLS 자리 →
    `SSL WRONG_VERSION_NUMBER`(막힘). 같은 원 IP 로 두 테스트 계정 실패 20건 → `login_source_alarm`(원 IP 가 살아 있어 경보도 의미 있다).
  - 본문 상한(d27 앱): ≈21MiB POST → 413 `{"detail":"request_too_large"}`, ≈19MiB → 상한 통과(앱 422 — 평문 비밀번호 거절).
  - 긴 응답: `/assets` JS 1,178,409B 를 15.7KB/s 로 65.7초 — 200·전량(HTTP/2). readTimeout 60s 는 요청 본문 읽기에만 걸린다.
  - keep-alive 경합: 본문 있는 POST 40회를 5.5초 간격(옛 uvicorn keep-alive 5s 바로 뒤) → 401 40 · **502 0**.
  - 라우팅: 모르는 Host(SNI unknown.example) → 404 + TRAEFIK DEFAULT CERT, IP 직접(SNI 없음) → 404 + 기본 인증서, `http://dms-k8s.local`(노드
    :80) → 404(DMS 라우터는 websecure 에만), Host 에 포트(`dms-k8s.local:8443`) → 200.
  - 설치기(새 코드, 라이브 — 단계 S1·S3·S8·S12): S1 WARN(control-plane 1대 — 테스트베드 정상)·「2. 입구」 IngressClass PASS·Traefik Ready 3/3,
    S3 PASS(platform/ingress dry-run), S8 PASS(ingress-probe 노드 셋 readyz 200·인증서 OK), S12 PASS(5절 Ingress·ingress-probe, 6절 VIP readyz 200,
    http 80 응답 없음 INFO — edge 에 80 VS 없음). 감지: traefik C reuse(Ready 3, trustedIPs 10.10.10.1/32, readTimeout 기본 60s note), ingress D=.
  - 실 Chrome: 관리자 30화면 × 1440·390 — 콘솔 오류·실패 GET·넘침·이상 글자 0.
- **testbed IaC**(`~/dms-dev/testbed` — 상세·게이트·라이브 결과는 그 저장소 `docs/ARCHITECTURE.md` §19.7, `README.md` 「Portal edge」): 새
  `roles/traefik` + `playbooks/traefik.yml` + `make traefik-dmsk8s`(dmsk8s 전용 — `traefik_enabled` 아닌 클러스터 거부, helm 없이 CRD install-once +
  템플릿 apply, 실행 이미지 ≠ 핀이면 거부), group_vars(`traefik_version`·`traefik_image`·`traefik_crd_url` 핀, dmsk8s `traefik_enabled`·
  `traefik_proxy_trusted_ips`·`traefik_tolerate_control_plane`·`portal_edge: traefik`·`portal_ingress: dms/dms-portal-traefik`, `dms_envoy_*`
  삭제), `tbinfo.py` EDGE_MODES = traefik·ingress(gateway·옛 키는 이유와 함께 거부, 풀 = 클러스터 모든 노드), `host-edge.sh` 백엔드
  `<c>_masters` → `<c>_nodes`, roles/common 의 cp443 펜스·port floor 를 걷는 은퇴 블록(태그 cp443 — 노드에 DMS Envoy 가 있거나 못 읽으면
  아무것도 걷지 않는다), validate·post-boot 의 traefik 모드 단언(노드마다 PROXY 없이 직접 readyz 200 — 예전 「PROXY 강제 curl 35/56」의 반대).
  오프라인 게이트: tbinfo 변형 15/15·host-edge 8/8·`haproxy -c` valid·템플릿 dry-run=server·은퇴 블록 하네스 13/13. 작업 사본에서 고쳐 진짜
  저장소에 sync(18경로)했고 **커밋은 사용자 몫**이다(job tmp `traefik/tb-tools/commit-testbed.sh` — 미커밋 경로가 기대 18개와 정확히 같을 때만
  pathspec 커밋; 2026-11-16 현재 미커밋).
- **남은 일 — 프로덕션(OA) 적용 순서**(사이트 운영 몫 포함):
  ① 지금 상태 확인: LB VS(VIP:443) 풀 멤버·포트, PROXY v2(OA 는 켜짐), rke2-traefik 의 websecure `proxyProtocol.trustedIPs`.
  ② Rancher 클러스터 편집 → chartValues 의 rke2-traefik(또는 HelmChartConfig `kube-system/rke2-traefik` 의 `spec.valuesContent`)에
     `ports.websecure.proxyProtocol.trustedIPs: ["<SE 주소>/32", …]`(forwardedHeaders 는 비움; 예: SE 두 대 <SE 주소 1>·<SE 주소 2> — 실제 주소는 사이트 운영 문서) — Traefik
     DaemonSet 이 노드별로 롤링 재기동한다. **LB 가 PROXY v2 를 붙이는데 trustedIPs 가 없으면 Traefik 은 그 헤더를 TLS 바이트로 읽어 연결이
     깨진다**(지금 OA 의 DMS VIP 가 깨져 있을 수 있는 이유).
  ③ dms-k8s pull → values.env 에서 `GATEWAY_*` 줄 삭제(남아도 알림 뒤 무시)·`PORTAL_DOMAIN` 확인 → `sh deploy/install.sh --context <ctx> --site
     <site>`(S8 이 Ingress 를 만들고 노드마다 확인한다) → 같은 커밋으로 포탈 빌드 → 릴리스(새 앱 — 본문 상한·keep-alive 120,
     deploy/README §4-2 5·6). 태그가 `live` 라 설치기는 앱 이미지를 바꾸지 않으니 **LB 를 옮기기 전에** 릴리스까지 끝낸다(deploy/README §2g-8 3).
  ④ LB 풀 = Traefik 이 도는 노드 전부 :443, 헬스 = TCP 443 또는 SNI·Host 가 `PORTAL_DOMAIN` 인 HTTPS /readyz(SNI 없는 HTTPS 모니터는 404 → DOWN).
  ⑤ 실증: 로그인 실패 보안 이벤트의 `client_ip` 가 사용자 PC 인지 — SE 주소로 찍히면 PROXY 가 쓰이지 않는 것이다(trustedIPs·SNAT 확인).
  ⑥ 그 뒤 옛 Envoy(DMS 가 깔았다면) 정리 — 위 런북 1~6. Gateway API CRD 는 Helm 소유(rke2-traefik-crd)라 남긴다.

### ✅ 입구 — 리스너 포트(GATEWAY_LISTEN_PORT)·PROXY off 모드·마스터 hostPort 충돌 감지(OA: Traefik 이 호스트 443, Avi PROXY v2 미지원) — **완료**(2026-11-14)

사용자 보고(OA 프로덕션): Rancher + Traefik 이 있는 RKE2 클러스터에서 Traefik 이 노드 호스트 443 을 쥐고 있고, Avi L4 는 PROXY v2 를 지원하지 않는다 —
VIP:443 → 마스터:443 이 DMS Envoy 에 닿지 않는다. DMS VIP 는 Rancher 와 별도. 이미지와 무관한 배포 층 변경이라 태그·릴리스 없음.

- **원인**: Envoy 는 hostNetwork 로 마스터 :443 에 직접 bind 한다(Gateway 리스너 443 + `useListenerPortAsContainerPort`). 다른 파드의 hostPort 443 은
  호스트 소켓이 아니라 CNI 의 DNAT 규칙이라 `ss`·S8 LISTEN 프로브에는 Envoy 만 보이는데, 그 포트의 패킷은 Envoy 보다 먼저 그 파드로 넘어간다.
  게다가 기본 `GATEWAY_PROXY_PROTOCOL=required` 는 PROXY v2 헤더 없는 연결을 끊는다.
- **`GATEWAY_LISTEN_PORT`**(values.env, 기본 443): Gateway 리스너 = Envoy 가 마스터 호스트에서 여는 포트 = LB 풀 멤버 포트(VS 는 그대로 VIP:443).
  `render.sh` 가 Gateway JSON 패치로 넣고 render.env 로 내보낸다 — `install.sh` S8(1024 이상이면 sysctl 프로브를 건너뛴다)·`host-net-probe.sh --port`·
  `verify.sh`·`preflight-cluster.sh` 가 그 값을 따른다. NodePort 범위(30000~32767)·Envoy 자체 포트(19000~19003)·k8s 구성요소 포트는 FAIL, 32768 이상은 알림.
  기본(키 없음)은 패치가 없어 기존 출력 그대로다. 권장 예 10443.
- **`GATEWAY_PROXY_PROTOCOL=off`**(상시 모드): ClientTrafficPolicy 의 `proxyProtocol` 을 통째로 빼 Envoy 가 PROXY 를 해석하지 않는다. `optional` 을 상시로
  쓰지 않는 이유 — L4 LB 는 바이트를 그대로 넘겨 클라이언트가 PROXY 헤더를 심어 원 IP 기록을 위조할 수 있다. 모든 사용자가 LB(SE) 주소로 묶이므로 prod
  템플릿의 `DMS_LOGIN_ALARM_SOURCE_FAILURES` 자리에 0 을 넣는다(평소에는 그 줄을 지워 base 값 그대로 — 원천 하나). 원 IP 는 기록·경보 전용이라
  (`auth.client_ip`) 판정은 그대로다.
- **hostPort 충돌 감지**: S1(preflight)·S8(SSA 전, `hostport_gate`)이 「마스터에 hostPort <리스너 포트> 를 쓰는 다른 파드」를 찾아 멈추고
  `GATEWAY_LISTEN_PORT` 를 가리킨다. hostNetwork 파드(자기 Envoy)·워커의 파드는 세지 않고, 목록을 못 읽으면 WARN(모름 ≠ 실패).
- **문서**: deploy/README §0(sysctl·방화벽·체크리스트)·§2g-1 values 표·§2g-4 「PROXY v2 를 못 붙이는 LB·마스터 443 을 다른 것이 쓰는 클러스터」·§2g-5·§3·§14,
  prod README 체크리스트 7, values.env.example.
- **검증**: 배포 계약 묶음 15개 파일 741 passed / 2 skipped(새 테스트: off 렌더·포트 렌더·잘못된 포트·임시 포트 알림·프로브 --port·S8 hostPort 4건·preflight).
  dmsk8s 에 테스트베드 값 + off + 10443 렌더를 server dry-run — 6개 객체 통과, 라이브 무변경(443·PROXY 필수 그대로), 표준 렌더 복원·guard exit 0.
- **테스트베드 실증**(2026-11-14 사용자 「테스트베드에서도 proxy v2 안쓰는 구성으로 실증해봐」): 호스트 edge haproxy 에서 `send-proxy-v2` 를 빼고 설치기
  S8 로 단계별 적용, LAN 릴레이(원 IP 192.168.75.215 ≠ haproxy 주소 10.10.10.1)로 측정. 단계: ① 기준(PROXY v2·required) 200, client_ip=192.168.75.215,
  위조 PROXY 헤더 → TLS 실패 ② optional + edge PROXY 없음: 200, client_ip=10.10.10.1 — 위조 헤더(6.6.6.6) **200 이고 client_ip=6.6.6.6 으로 기록**(optional
  을 상시로 쓰면 안 되는 근거 실측) ③ off(443): 200, client_ip=10.10.10.1, 위조 헤더 → TLS 실패, VIP·Tailscale 200, 실 Chrome 30화면 × 1440·390 문제 0
  ④ off + `GATEWAY_LISTEN_PORT=10443`(풀 멤버 :10443): S8 PASS(sysctl 건너뜀·:10443 LISTEN), S12 PASS, preflight 의 hostPort·포트 줄 정상, 200, 위조
  → TLS 실패. 앱 보안 이벤트 client_ip=10.10.10.1 — 그리고 두 계정의 실패 20건이 LB 주소 하나로 묶여 `login_source_alarm` 이 실제로 울렸다
  (테스트베드 고정 오버레이에는 경보 0 이 없다 — prod 렌더가 off 에서 0 을 넣는 이유). 포트 전환 중 3초 간격 요청 2건 실패(약 5~10초, LB 헬스체크
  전환 — deploy/README §2g-4 적용 줄에 기록). 되돌림: 443 → edge 원본(send-proxy-v2) → required — `host-edge.sh status` 전부 OK(설정 = IaC 렌더),
  client_ip=192.168.75.215, guard exit 0. Envoy 파드는 재시작하지 않았다(설정만 바뀜).

### ✅ OA 사이트(RKE2) 설치 — Gateway API 하한 v1.5.0·묶음 dry-run 오류 줄·control-plane 라벨 값(CONTROL_PLANE_LABEL_VALUE) — **완료**(2026-11-13)

OA 사이트(Rancher/RKE2) 운영 쪽이 설치 중 찾은 세 결함을 진단·패치 설명으로 넘겼다(패치 파일은 이 호스트에 없어 설명대로 구현). 이미지와 무관한
배포 스크립트 변경이라 태그·릴리스 없음.

- **Gateway API 재사용 하한 v1.6.0 → v1.5.0**(88746eee): RKE2 의 rke2-traefik-crd(Helm 소유)가 v1.5.1 을 미리 깔아 판정 X 로 S8 이 거부됐다.
  `install-detect.GWAPI_FLOOR`·X 문구·docstring·deploy/README §2g 감지 줄. DMS 가 쓰는 GatewayClass·Gateway·HTTPRoute 는 v1 standard 라 v1.5 에도 있다.
- **묶음 dry-run 오류 끝 줄**(88746eee): 없는 kind 가 둘 이상이면 kubectl 이 `[a, b, c]` 로 묶어 마지막 줄이 「ensure CRDs are installed first]」 —
  첫 설치에서 EG CRD 3종이 한꺼번에 없으면 S3 계획이 FAIL 했다. `_classify` 가 `rstrip("]")` 로 비교한다.
- **control-plane 라벨 값**(9d545216): RKE2 는 `node-role.kubernetes.io/control-plane=true` 인데 base·prod nodeSelector 는 `""` 라 레지스트리·PG·
  Envoy·api/controller 가 Pending 이었다. values.env 선택 키 `CONTROL_PLANE_LABEL_VALUE`(기본 빈 값 = 기존 출력 그대로, k8s 라벨 값 문법 밖 FAIL)가
  앱 patch-placement·platform/registry·platform/gateway(EnvoyProxy)·platform/db-cluster(`PG_PLACEMENT=control-plane`)의 그 값을 바꾼다. 키 존재로만
  고르는 곳(DoesNotExist·preflight·프로브)은 무관. 문서: deploy/README §0·§2f-1·§3, values.env.example.
- **검증**: 배포 계약 묶음 8개 파일 566 passed / 2 skipped(kubectl kustomize 실제 렌더로 네 층 nodeSelector "true" 확인 포함).

### ✅ 포탈 전수조사 — 버그 92건 수정(high 0 · medium 12 · low 80) d26 — **완료·실증**(2026-11-11)

사용자 지시: 「포탈 기능중에 작은 버그들이 있을수 있으니 전수조사 해봐」 → 「92건 모두 고쳐」.

- **조사**: 코드 10영역(단일 작업 제출·작업 목록/상세·배치·k8s·사용량·대시보드/노드·빌드/릴리스/컨트롤·계정/인증/보안/메일/감사·
  스토리지/정책/denylist·공통 app/lib/components — 프런트와 대응 백엔드 라우트) + 라이브 2(관리자 mason·일반 사용자, 실 Chrome
  1440·390, GET 외 요청 차단). 후보 113 → 항목마다 반박 검증(기각 1: 감사 로그 `seq:110-110` 은 정상 표기) → 같은 원인 15묶음 병합 →
  92건(CONFIRMED 85 · PLAUSIBLE 7, BACKLOG 등재 2).
- **medium 12**: B01 아이디 끝 공백이면 가입·재설정 인증번호가 늘 실패(요청은 trim, 제출은 원문) — 정규화 한 함수 · B02 denylist 대상
  앞뒤 공백 저장으로 차단 무효 — strip·빈 값 거절·옛 행 strip 비교 · B03 rm 에서 켠 quiet 가 사용자 sync 바디에 실림 — 칸이 보이는
  관리자만 · B04 컨트롤 상태 폼이 창 포커스 복귀 때 미저장 입력을 덮음 · B05 「진행 중 작업」·KPI 「실행 중·대기」가 최근 24h 생성
  잡만 셈 — `/api/admin/metrics/jobs` 에 창 없는 `live_by_state`(`MetricsRepository.live_job_states`, deploy/README §14-1) · B06 노드
  신선도가 안 갱신되고 멈춘 노드도 「정상」 · B07 배치 상세 404·오류 때 빈 뼈대 + 2.5초 무한 재조회 · B08 전체 작업 폴링 한 번 실패에
  표 전체가 오류 한 줄 — 표 유지 + 「자동 갱신 실패」 띠 · B09 scan 「스캔한 파일」(요청 상세)·「파일 수」(배치 펼친 행)가 실제로는
  전체 항목 수 — 둘 다 「스캔한 항목」(요청 상세 KPI 는 보조 줄 「디렉터리·링크 포함」) · B10 상태 필터 「Running」은 늘 0건(요청
  상태는 Running 이 되지 않음) — 목록에서 뺌 · B11 처리량 라벨이 UTC — KST 표시, 일 버킷은 KST 날짜로 접음(`metrics_series.kst_day_buckets`) · B12 k8s 「스냅숏 기준」 줄 390px 넘침.
- **low 80(묶음)**: 조회 실패·로딩을 0·빈 상태로 그리던 곳(null ≠ 0), 다음 동작 뒤에도 남는 오류 문구(reset), 응답 상한 잘림 안내
  (배치 100·빌드 50 — limit+1 감지), 네트워크 단절·비JSON 응답 문구(새 사유 코드 양쪽 등록), 로그인 뒤 원래 화면 복귀, 전체 작업·PV 탐색
  필터를 주소에, 모바일(md 미만) 사이드바를 「메뉴」 버튼으로 여닫기(BACKLOG 해소), 요청 상세 배치 링크(존재·이름, BACKLOG 해소),
  390px 넘침·꺾임, dms-ssc(LDAP) 시절 문구, 정책 입력 검증(큐 공백·int4 상한), denylist 재추가 = 사유 갱신, storage_in_use 수정/삭제
  문구 분리, 비활성 계정 로그인·재설정, 보안 이벤트 누락 유형, 레지스트리 태그 자연 정렬·digest 공유 삭제 보고, 노드 스파크라인 시각
  축·보고 공백, TimeSeriesChart 점 수 감소 크래시, 상태 배지 색 등. 결정 갈림길은 「최소 수정·덜 놀라운 쪽」으로 골랐고 코드 「왜」
  주석에 남겼다.
- **방법·검증**(소스 73d29c1e, 171 파일): 10그룹 병렬 수정(파일 소유 분담) → 그룹 리뷰·반영 → 통합 게이트 → 교차 리뷰(규약·문구,
  3건 반영) → 최종 게이트 — 백엔드 전체 5641 passed / 15 skipped, vitest 1999 passed, tsc -b 0, build 외부 URL 0, e2e 32 passed.
- **배포·실증**: 포탈 빌드 d26(3종, 210초) → 릴리스 agent → api → controller → job-image 모두 Applied, 오버레이 bump b4bfd83f(guard
  exit 0). 라이브(실 Chrome, GET 외 차단): 관리자 30개 화면 × 1440·390 순회 콘솔 오류·실패 GET·넘침·이상 글자 0; B85 390 첫 화면 =
  본문 h1(y=126)·「메뉴」 여닫기·링크 뒤 닫힘; B10 필터에 Running 없음; B31 `/jobs?state=Succeeded` 새로고침 유지; B45 호출 노드
  필터에 k8s-w2; B05 `live_by_state` 응답; B12 PV 탐색 390 넘침 0; B09 scan 상세 「스캔한 항목 2개 · 디렉터리·링크 포함」.

### ✅ 포탈 k8s 연동 — 클러스터를 개요에서 별도 「클러스터」 탭으로 분리 d25 — **완료·실증**(2026-11-11)

사용자 지시: 「포탈 k8s 개요에서 클러스터를 다른 탭으로 빼줘」.

- **구성**(소스 0ae6db14, 프런트·문서만 — 백엔드 무변경): 새 탭 `/admin/k8s/clusters`(`frontend/src/features/k8s/ClustersPage.tsx`) —
  클러스터 카드(지금 동기화·편집·이력·삭제, 경고·동기화 결과), 머리 동작 「모두 지금 동기화」「클러스터 등록」, 요약 줄, 빈 상태 안내.
  개요는 집계 타일(상태별 개수)·호출 노드와 주기만 남기고, 클러스터 타일·빈 상태 힌트·호출 노드 카드의 실패 클러스터 이름이 새 탭으로
  연결된다. 탭 6개: 개요 · 클러스터 · StorageClass · PV 탐색 · 사용자 권한 · 동기화 이력. 라우터·브레드크럼·e2e 레이아웃 순회에 새 경로,
  `k8s_mapping_unknown` 문구 「k8s 연동 → 클러스터」, 문서 deploy/README §6-5·§7-1·deploy/testbed/README §5-3·ARCHITECTURE 탭 목록.
- **리뷰에서 잡은 것**(6건 → 전부 반영): 가입 신원 경고(메일 stub 동안 남의 이름 가입 → 그 사람의 PV 권한)가 등록 버튼과 함께 옮겨 가지
  않아 개요에만 있던 것 — 등록하는 화면(클러스터 탭)에도 표시; 탭 6개가 되며 375/390 폭에서 현재 탭이 밖으로 밀리던 것 — 현재 탭을 보이게
  스크롤; 「클러스터 없음」 안내 중복·문구 통일; 개요 실패 문구 → 새 탭 링크; deploy/README 문구 2곳.
- **검증**: vitest 1872 passed, tsc -b 0, e2e 32 passed, 빌드 dist/index.html 외부 URL 0, 백엔드 문서 계약 418 passed.
- **배포·실증**: 포탈 빌드 d25(3종, 210초) → 릴리스 agent → api → controller → job-image 모두 Applied, 오버레이 bump f2d44a47(guard exit 0).
  라이브(실 Chrome 1440·390): 탭 6개, 개요에 클러스터 카드·등록 버튼 0·호출 노드 카드 유지, 집계 타일 → `/admin/k8s/clusters`(카드 dmsk8s·
  dmsk8s2, 브레드크럼 「… > k8s 연동 > 클러스터」), 가입 신원 경고 표시, 가로 넘침 0px, 콘솔 오류 0.

### ✅ 지운 k8s 클러스터의 동기화 이력 — 보존 정리 결함·호출 노드 카드에서 제외 d24 — **완료·실증**(2026-11-11)

사용자가 포탈 k8s 연동 개요의 주 호출 노드 카드를 붙여 「의도된 거야?」 → 「코드로 고치기」.

- **증상**: 주 호출 노드 k8s-w1 카드에 「마지막 실패 6일 전 · fv-inv-c1 수동 · API 서버 인증서를 검증하지 못했습니다 — …방화벽에 이 IP 를
  허용하세요」가 계속 남았다. `fv-inv-c1` 은 2026-11-04 기능 전수 점검이 TLS 실패 표시를 보려고 일부러 틀리게 등록했다 지운 임시 클러스터다.
- **원인**: `repositories.k8s.K8sRepository.delete_cluster` 는 run 이력을 보존 정리에 맡기는데, `prune_runs` 는 클러스터 이름마다 최근
  50건과 마지막 applied 를 남겨 run 이 50건 미만인 지운 클러스터의 이력을 영영 지우지 않았다(점검 보고서의 「보존 정리 대상」 판단이 틀렸다).
  `node_run_stats` 의 노드 단위 판정은 지운 클러스터의 run 까지 셌다.
- **수정**(소스 079e7199): `prune_runs` — 등록되지 않은(지운) 클러스터 이름의 종단 run 은 보존일(기본 30일)이 지나면 50건·마지막 applied
  보존 없이 전부 지운다(묶음 질의 안의 NOT EXISTS 판정, 활성 run 은 그대로; 등록된 클러스터는 종전과 같다). `node_run_stats` — 노드 단위
  `last_ok_at`·실패 묶음은 지금 등록된 클러스터의 run 만 센다(`last_claim_at` 「마지막 호출」은 모든 run, 응답 모양 그대로). 같은 이름으로 다시
  등록하면 그 이름의 옛 run 을 다시 센다(deploy/README §6-5 「장애 표시」).
- **검증**: 테스트 3건 추가(+1 기대 변경 — 옛 코드에서 실패 확인), 백엔드 5610 passed / 15 skipped, PG(일회용 16) 관련 121 passed, vitest k8s
  104 passed, tsc OK. 리뷰 2관점 결함 0 — 정확성은 옛 구현과의 무작위 차등 테스트(SQLite·PG 각 49건)로 확인.
- **배포·실증**: 포탈 빌드 d24(3종, 210초) → 릴리스 agent → api → controller → job-image 모두 Applied, 오버레이 bump e33bf49c(guard exit 0).
  라이브 개요: 주 호출 노드의 `last_fail_cluster` 가 `fv-inv-c1`(11-04) → 등록된 `dmsk8s2` 의 11-03 `k8s_sync_expired`(그 뒤 성공이 있어
  「해결됨」 표시)로 바뀌었다. `fv-inv-c1` run 행은 보존일(12-04 무렵)이 지난 뒤 첫 보존 정리에서 지워진다.

### ✅ 문서 전면 정리 — 프로덕션 기준 배포 런북·테스트베드 분리·CHANGELOG 이관·ARCHITECTURE 심볼 앵커·base 중립화 — **완료**(2026-11-06)

사용자 지시 「전체 수정 및 업데이트 하고 마지막에 최종 검수까지 진행해. 배포 문서는 내가 실제 프로덕션 환경에서 배포할때 참고 가능하게
해야해. 테스트베드에 국한되어서는 안된다는 말이야, 명심해.」. 판단 기준은 하나였다: **사이트 운영자가 이 문서들만 보고 프로덕션 클러스터에
설치·운영할 수 있는가**. 먼저 다섯 가지를 만들었다: 문서 감사, 코드에서 끌어낸 프로덕션 설치 절차(근거 = 파일·심볼), prod 렌더 대조,
참조 전수(deploy/README 를 가리키는 것만 566건), 테스트베드 내용 목록. 그 위에 설계를 세웠다(번호 계약·지울 것/옮길 것 표·파일 소유
분할·최종 검수 체크리스트 — 세션 job tmp `docs/plan.md`). 고치는 일은 담당을 나눠 병렬로 했다. 앱 동작은 바뀌지 않는다(바뀐 것은 base
설정 3값과 빌드 스크립트 가드 — 아래 「문서 밖 수정」).

- **새 배치**:
  - `deploy/README.md` = 「DMS 배포·운영 런북 (dms-k8s — 프로덕션 기준)」(2,862 → 3,037줄 — 검수 고침 뒤).
    - 머리에 사이트 중립 자리표시자 표(`<ctx>`·`<REGISTRY>`·`<SHARED_DIR>`·`<FQDN>`/`<VIP-A>`·`<LOCAL_ADMIN>`·`<DMS_TAG>`·`<빌드 노드>`·
      `<사내CA>`·`<마스터1..3>`)를 두었다.
    - 새로 쓴 장:
      - §0 프로덕션 배포 한눈에: 정할 값, 노드·호스트 요건, 포트·NTP·DNS, 승인 항목과 DMS 가 만드는 클러스터 범위 객체 전수(prod 렌더에서
        추출), 전체 순서 표(단계마다 호스트·명령·성공 판정·실패 시 절), 책임 경계, 운영 지도.
      - §3 적용 규칙(적용은 설치기 하나)·컨텍스트·가드 종료 코드·설정을 바꾸는 네 길.
      - §3b 시크릿·TLS·토큰의 의미·회전. §4 업그레이드. §5 롤백·복구(재해 복구 포함).
      - §6 설치 뒤 설정 6-1~6-8(사용자에게 열기 전 확인표까지). §7 스모크 시험.
      - §10 계정·권한·인증(10-7 웹 인증 하드닝은 같은 내용). §14 일상 운영·정기 작업 달력·문제 해결 색인·에이전트 지표.
    - 공유 장(§1·§2·§2b·§2d~§2h·§8·§9·§11·§12·§12b)은 일반화했다. 테스트베드 값은 「예(테스트베드)」·「테스트베드 실측」 표시가 붙은 줄에만
      남겼다.
    - 옛 맨 앞의 「이미지 빌드 전 게이트」는 §8 안으로 옮겼다.
    - §2b·§9 의 항목을 `### 2b-N`·`### 9-N` 제목으로 바꿨다 — 바깥의 `§2b-1`·`§9-7` 참조가 실제 제목에 닿는다.
  - `deploy/overlays/prod/README.md` = 한 화면 설치 체크리스트(190 → 275줄).
    - 단계마다 [어느 호스트]·성공 판정(RESULT·exit)·실패 시 갈 절을 적었다.
    - 새 단계: 5b) 메일, 6) deploy/README §6 차례, 7) 다시 검증.
    - §0 사이트 준비에 8~12 를 더했다: 데이터·PV 마운트, `fs.protected_*`, 방화벽·NTP·DNS, Volcano 선설치, 메일 릴레이 준비.
    - 선행조건 표에 노드 수·시간·방화벽·릴레이 행을 더했고, 새 절 「설치 뒤 운영」을 두었다.
    - 첫 관리자 명령은 `install.sh` S13 출력과 글자가 같다(스크립트로 대조).
  - **신규** `deploy/testbed/README.md`(596줄) = k8s 테스트베드(dmsk8s)의 DMS 쪽 운영. 11절: 범위·안전 규칙, 값 대응표, 오버레이 계약,
    설치·재구축 순서, 시드·픽스처, 일상 운영, 되돌림·보존물, 시나리오, 검증 도구, 한계, 옛 절 대응. 인프라는 testbed IaC 저장소를 가리키기만
    한다.
  - CHANGELOG 이관:
    - 이 파일에는 2026-10-21 분리 이후만 남겼다(4,999 → 2,067줄). 머리·태그 계열 표·인덱스는 새로 썼고, 「기록이 없는 옛 작업」 보충 표
      (슬라이스 32·36·37)를 더했다.
    - 옛 부분은 **신규** `docs/history/changelog-2026-09-to-10-21.md`·`docs/history/changelog-2026-08-slices.md` 로 옮겼다. 본문은 바이트
      그대로이고, 머리 3줄에 원본 줄 범위를 적었다.
    - `docs/history/README.md`:
      - 설계 29편과 이관본 2편의 색인(상태 a/a*/b, 대체한 곳).
      - 플랜 복구 명령 정정.
      - 저장소에 없는 설계를 가리키는 코드 인용 28줄 정리.
  - `docs/ARCHITECTURE.md`:
    - `파일:줄` 앵커 178건을 `모듈.심볼` 로 바꿨다. 줄 번호가 89건 어긋나 있어서, 심볼은 줄 번호가 아니라 서술로 골랐다.
    - 다른 워크트리의 절대 경로 13건을 `src/dms/…` 로 바꿨고, 1,500자 넘는 줄 17개를 나눴다.
    - §10 「k8s 연동(RBAC·PV 맵핑)」을 새로 냈다. §1~§9 와 불변식 번호는 그대로다.
    - 낡은 사실을 고쳤다:
      - 서브시스템 7, 리포지토리 18.
      - 오버레이는 prod·testbed-k8s 뿐.
      - 빌드 흐름 = 소스 hostPath 스냅숏, `FROM <DMS_BUILD_REGISTRY>`.
      - scan 권한(`scan_unrestricted_only`), 지워진 `syncChoices`, wiring 팩토리 6.
  - `docs/BACKLOG.md`(871 → 640줄):
    - 기준일을 2026-11-06 으로 바꿨다.
    - 해소 블록은 다른 파일이 「제목」으로 인용하는 13개만 한 줄 묘비로 남겼다.
    - dms-ssc·LDAP 전용 8건과 낡은 사실을 지우고, 중복을 합쳤다.
    - 새 소절 「2026-11-06 문서 전면 정리에서 나온 후속 — 신규 14」를 두었다.
  - `CLAUDE.md`:
    - 문서 지도를 9행으로 늘렸다(prod 체크리스트·런북·testbed README·이관본·history README·frontend README).
    - 적용 경로 = 설치기, guard-images 의 current-context 대체, base `DMS_BUILD_*` 자리표시자를 적었다.
    - 테스트 수치를 측정값으로 바꿨다(백엔드 5600+·vitest 1858·e2e 32).
    - 금지 규약 문장(HEAD 23개)은 하나도 잃지 않았다.
  - `frontend/README.md`(71 → 38줄): 백엔드는 `src/dms/api/` 패키지, scripts, e2e 하네스, 규약 4개를 적고 낡은 GHSA 서사는 지웠다.
- **번호 정책**(deploy/README 머리·「부록 Z. 옛 절 번호 대응」): 설치기 출력·다른 파일·testbed 저장소가 가리키는 번호를 지킨다.
  - 공유 장은 번호·제목 접두를 그대로 둔다(`## 2g.`, §2g 안의 테스트 리터럴 포함).
  - 옛 테스트베드 장 가운데 같은 주제의 프로덕션판이 있는 §0·§3·§3b·§4·§5·§6·§7·§10 은 그 판으로 다시 쓴다.
  - 주제가 다른 §2c·§2d-5·§2e-6·§2f-8·§2g-7·§12c·§12d·§13 은 **은퇴**한다. 다시 쓰지 않는다 — 옛 참조가 엉뚱한 내용에 조용히 떨어지지 않게.
  - 새 장은 §14 다.
  - 부록 Z 는 옛 번호마다 지금 위치(deploy/README·testbed README·CHANGELOG)를 준다.
  - 설치기 출력 포인터 가운데 바뀐 것은 `install.sh` 의 §13-9 → §6-5 하나다.
- **바로잡은 사실**(코드 대조):
  - **메일 게이트는 사용자에게 열기 전**:
    - prod 렌더에는 `DMS_MAILER_BACKEND` 가 없어 코드 기본 `stub`(`config.Settings.mailer_backend`)으로 돈다.
    - stub 은 인증번호를 응답에 돌려준다(`routes_auth.request_verification_code` 의 `stub_code`). k8s 신원 모드에서는 아이디 = RoleBinding
      ID 라, 이대로 열면 누구나 남의 아이디로 가입해 그 PV 권한을 얻는다.
    - 옛 문서와 S13 체크리스트에는 메일 단계가 없었다.
    - 이제 deploy/README §6-2·prod README 5b)·S13 줄이 이 단계를 둔다. 릴레이 허용 클라이언트 = dms-api 가 뜨는 마스터 IP, 사이트
      patch-config 에 `DMS_MAILER_BACKEND: "knox_relay"` 고정.
  - **사용자 연산은 sync·rm**: 옛 prod README 의 「사용자 연산은 sync·scan·rm」과 옛 권한 표 첫 행이 틀렸다.
    - prod 는 `DMS_USER_ALLOWED_OPERATIONS: "sync,rm"` 이고 scan 은 특권 운영자만 한다.
    - 특권 목록 예 `root,admin,mason` → `root,admin,<LOCAL_ADMIN>`(∪ 계정 화면 부여).
  - **빌드 노드 요건**: 빌드·프리플라이트 파드는 toleration 이 없고(`build_manifests` 0건) privileged 다.
    - 그래서 빌드(= 인터넷) 노드는 NoSchedule taint 없는 워커여야 하고, privileged 를 허용해야 하고, 로컬 체크아웃이어야 한다(git worktree
      면 SHA 가 `unknown`).
    - deploy/README §0-2·§8 0)·S13 줄에 적었다.
  - **guard-images 컨텍스트 대체**: 옛 prod README 「current-context 는 어느 스크립트도 읽지 않는다」와 옛 주석 「install.sh 는 현재
    컨텍스트로 apply」가 틀렸다.
    - `install.sh` 는 늘 `--context` 로 부른다.
    - `guard-images.sh` 는 `--context`·`KUBE_CONTEXT` 가 둘 다 없으면 current-context 를 읽는다.
    - 사이트는 `deploy/overlays/prod/kube-context` 로 고정할 수 있다(render.sh 가 층마다 복사하고 install.sh 가 대조 — §3-2).
  - **시드 예의 `x-dms-actor` 결함**: 옛 §6 시드 curl 은 `x-dms-actor: seed-script` 를 실었다.
    - 공유 토큰 경로는 `node:<이름>` 만 받아서 400 `invalid_actor` 다(`api.auth.current_identity`). 그대로 따라 하면 스토리지·정책 등록이 전부
      실패했다.
    - §6-4 는 이 헤더를 싣지 않는다.
  - 그 밖:
    - `DMS_ADMIN_TOKEN` 은 첫 관리자 뒤에도 admin 계정을 만들 수 있는 상시 자격이다(`routes_auth.create_admin_account`). ns dms 의 시크릿
      읽기 권한 = DMS 최고 권한이다(§3b-3).
    - 되돌림에서 `kubectl rollout undo` 금지를 일반 규칙으로 올렸다(§5-1).
    - 자원 예산은 설계 표가 아니라 렌더 값을 인용한다(dms-api requests 100m/256Mi).
    - 시크릿은 S2 가 만든다. 옛 §3b 의 ssc 5키는 지웠고, `20-secret.example.yaml` 의 URL 은 CNPG 모양으로 바꿨다.
    - 세션 쿠키 수명 14일과 로그아웃의 한계를 적었다(§10-6).
    - 테스트베드 쪽:
      - 빌드 소스는 `/home/mason/dms-dev/dms-k8s` 다(옛 `/home/mason/dms-dev/dms` 는 틀렸다).
      - preflight 는 `PORTAL_VIP` 를 읽지 않는다(읽는 것은 verify.sh).
      - 레지스트리 주소 대조의 개수 표기를 render.sh 머리 주석의 정의(설계 7-4 의 여섯 곳 = 렌더가 보는 다섯 + DB 잡 이미지)에 맞췄다.
      - Bearer 제출은 403 이라 시나리오를 봉인 세션 로그인으로 다시 썼다.
      - launcher 파드에는 `dms.io/job-id` 라벨이 없다.
- **문서 밖 수정**:
  - **base 중립화**(`deploy/k8s/20-config.yaml`): CLAUDE.md 「base 매니페스트는 사이트 중립, 실 태그는 오버레이에」 위반을 풀었다.
    - `DMS_PRIVILEGED_REQUESTERS` `root,admin,mason` → `root,admin`(코드 기본과 같게).
    - `DMS_BUILD_REGISTRY` `pkg-01:5000` → `set-by-overlay.invalid`.
    - `DMS_BUILD_BUILDER_IMAGE` → `set-by-overlay.invalid/buildah:set-by-overlay`.
    - 주석도 사이트 중립으로 바꿨다.
    - 두 오버레이가 이 값을 덮으므로 **렌더는 바이트 동일**하다: HEAD 대비 prod in-cluster 12층·prod external 11층·testbed-k8s 12층이
      IDENTICAL(base 만 펼치면 바뀐 줄은 이 3줄뿐).
    - 사본의 prod 템플릿에서 `DMS_BUILD_REGISTRY`·`DMS_BUILD_BUILDER_IMAGE` 줄을 지우면 render rc 2 다.
  - **`deploy/docker/build-and-push.sh` fail-closed**: `REGISTRY="${REGISTRY:-pkg-01:5000}"` → `: "${REGISTRY:?…}"`.
    - HEAD 는 REGISTRY 없이도 rc 0 으로 `pkg-01:5000/dms:dev` 를 빌드·push 하러 갔다(다른 사이트에서도 테스트베드 주소로).
    - 지금은 rc 1 이고 podman 호출이 0건이다.
    - 머리 주석의 pkg-01·10.10.10.30 예도 지웠다.
  - **`install.sh` S13 체크리스트**: 순서는 첫 관리자 → **메일(Knox 릴레이) 줄(신설)** → 컨트롤 상태(무 taint 워커·로컬 체크아웃) →
    k8s 연동(§6-5 — 등록 전 stub 창 가입 계정 확인) → 사본 → 스모크 → Avi 헬스·`--only S12`(`--resolve` 라 DNS 전) → 열기 전 확인표 →
    DNS → 사용자 공지. 머리 주석도 일반화했다.
  - **출력·주석 포인터** — 새 절 번호로 맞췄다:
    - guard-images·render·push-images·shared-storage-probe·host-net-probe.
    - prod 오버레이 파일(값 줄·`REPLACE_` 토큰은 그대로).
    - testbed-k8s 오버레이 3파일, `dms-shared-fs.yml`(주석만 — `-e dms_cluster=dmsk8s` 필수를 적었다), `20-secret.example.yaml`.
  - **깨진 인용 3건**: `build_manifests` 의 `FROM` 주석(+ `_SCRIPT` 셸 주석 1곳 — 동작 같음), `routes_usage` docstring,
    `test_planner_path_conflict` 주석.
  - **검수 1차 고침**(같은 날):
    - `deploy/docker/Dockerfile.agent` 의 ARG 기본(테스트베드 레지스트리 `:dev`)을 지웠다 — 두 빌드 경로가 늘 `--build-arg` 로 준다.
    - `tests/test_config.py` 의 예시 시크릿 거부 시험이 `deploy/k8s/20-secret.example.yaml` 을 직접 읽는다(옛 URL 사본이 남아 그물이 빠졌었다).
    - base `kustomization.yaml`·`40-api.yaml`·`50-agent-daemonset.yaml`, prod `patch-remove-ingress.yaml`, testbed-k8s `patch-config.yaml`·
      `kustomization.yaml` 주석, `pg-path-probe.py` 의 WARN 문구(테스트베드 실측 표시).
    - deploy/README: 정책 예시가 migrate 시드를 1시간·미리보기 무제한으로 덮던 루프를 GET→수정→PUT 으로, 업그레이드는 설치기 전체 실행
      (플랫폼 층), 재해 복구의 `live` 태그·옛 PGDATA 이동을 필수로, 메일 게이트 전 VIP 직접 접속 창, 첫 빌드의 사내 CA 한계(BACKLOG ⑦
      「`build-and-push.sh` 사내 CA 주입」), 관리 토큰의 파드 env 노출, 평문 로그인 검증 curl 의 content-type 등.
  - **테스트**:
    - `test_release_manifest_contract`: `_IMAGE_KEYS` 에 `DMS_BUILD_BUILDER_IMAGE:` 를 더하고 `seen >= 8` 로 올렸다. 새
      `test_base_config_carries_no_site_registry_or_operator_name`.
    - `test_prod_k8s_overlay`: 새 `test_rendered_prod_layers_carry_no_testbed_value`·`test_prod_patch_config_overrides_every_site_key_of_the_base`.
    - **신규** `tests/test_build_and_push_script.py`(4개):
      - REGISTRY 가 없거나 빈 값이면 rc≠0 이고 외부 명령은 0건이다.
      - REGISTRY 를 주면 세 이미지가 같은 REGISTRY·TAG 를 쓴다.
      - 가드 앞의 코드 줄은 `set -euo pipefail` 뿐이다.
    - 새 테스트 가운데 5개가 HEAD 에서 빨간불인 것을 확인했다.
- **검사·테스트**(담당별, 한 번에 파일 묶음 하나씩):
  - 계약 묶음(prod 렌더 계약 12파일 + build-and-push): 400 passed / 1 skipped(HEAD 393 + 새 7).
  - 묶음별 통과 수:
    - test_install_stages 130, test_gateway_contract 64, test_gateway_contract + test_addons_render 84 passed·1 skipped.
    - test_check_scripts_context + test_shared_dir_contract 54, test_prod_k8s_overlay + test_platform_registry_contract 102.
    - test_push_images + test_shared_storage_probe + test_guard_images_context 159.
    - test_overlay_render_contract + test_testbed_k8s_overlay + test_release_manifest_contract + test_config 78, test_platform_db_contract 106.
    - test_planner_path_conflict + test_build_proxy 52, test_api_usage_unregistered + test_api_spa 36.
  - 수정한 셸은 `dash -n` 통과.
  - 문서 검사 스크립트:
    - deploy/README: 테스트베드 토큰이 「테스트베드」 표시 없이 나오는 줄 0, 1,000자 넘는 줄·줄 끝 공백·표 칸 수 어긋남 0.
    - 문서 안 § 참조는 모두 실제 제목에 닿는다. 다른 파일 → deploy/README § 참조 285건 가운데 안 닿는 3건은 모두 일부러 옛 번호를 쓴 곳이다.
    - ARCHITECTURE 심볼 검증(`arch_symbols.py`) PASS: file:line 0, 심볼·파일 754건 실재, /home 0, 1,500자 넘는 줄 0, 바깥 인용 앵커 유지.
    - 이관본 바이트 동일(09~10-21 2,053줄·08 958줄)과 인덱스 해소 PASS.
    - CLAUDE.md 식별자 109개, frontend README 식별자 34개가 모두 실재.
  - 최종 검수(plan §8 의 1~11):
    - prod 렌더: 작업 트리 사본에 예시 values 를 넣어 `render.sh prod` rc 0, 11개 층 모두 kustomize 성공. 비주석 줄의 테스트베드 토큰 0건이다
      (CNPG CRD 설명문의 상류 URL `…/volumes/cephfs/…` 15건은 제외).
    - 테스트: 계약 묶음 15파일 552 passed / 1 skipped. 백엔드 전체 5607 passed / 15 skipped(8분 3초 — 기준 5600 에 새 테스트 7).
      vitest 1858, e2e 32(17 파일)는 목록으로 셌다.
    - 참조: 설치기·스크립트가 출력하는 포인터 112건이 모두 실제 절에 닿는다. 남은 미해소는 일부러 쓴 옛 번호(부록 Z·testbed README §11·이 항목),
      plan 의 예외(CHANGELOG 옛 기록·testbed 저장소), 스캐너 오탐뿐이다.
    - 문서 위생: 링크·줄 길이·줄 끝 공백·표 칸 0건. CLAUDE.md 금지 문장 손실 0, 지도 경로 모두 실재. ARCHITECTURE 심볼 검증 PASS. 이관본 바이트 동일.
    - 검수 고침: prod README 머리에 「파일 이름 없이 쓴 § 는 deploy/README 의 절」 한 줄, testbed README 「§1-5」 → 「§1 의 5」, 이 항목의 줄 수.
- **남은 것**:
  - 문서에 「미검증」으로 표시한 단계(→ BACKLOG 신규 14 의 14):
    - 시크릿 회전 명령. DB 비밀번호 회전 절차는 없다.
    - PG minor 를 설치기로 올릴 때의 플래그, 레지스트리 주소 변경, 옛 이미지가 새 스키마에서 도는지, EG 다운그레이드.
    - 재해 복구 순서 전체(§5-6).
    - port-forward 한 localhost 에서 Secure 쿠키 로그인, 파드 송신 SNAT(릴레이 허용 IP).
    - 세션 14일(이미지의 Starlette 판이 고정돼 있지 않다), 봉인 로그인 예(실행해 보지 않았다), egress 의 CDN 도메인.
    - 테스트베드 README 의 순서 조합, ro 마운트 빌드, 옛 base 되돌림, dmsk8s 가짜 릴레이.
  - 기능 후속은 BACKLOG 신규 14:
    - values 튜닝 키, 렌더 형식 검사, S12 메일 stub WARN.
    - 봉인 로그인 도우미, 관리 토큰 회전·끄기, 빌드 파드 tolerations, TLS 만료 감시.
    - `config` build_registry 코드 기본 `pkg-01:5000`, testbed 도구의 ssc 기본값, base 46/47.
    - dms-api requests, testbed 저장소 후속, 감사 4건.
  - deploy/README 는 목표 분량(2,200~2,400줄)보다 길다(3,037줄). 프로덕션 장(§0·§3~§7·§10·§14)이 약 1,000줄이고, 설치·복구 손 절차
    (§2e~§2g·§12)는 줄이지 않았다.
  - testbed 저장소(`~/dms-dev/testbed`)의 옛 번호 참조와 낡은 DB URL 서술은 이 저장소 세션이 고치지 않는다(부록 Z·testbed README §11·BACKLOG
    신규 12).

### ✅ 라이브 기능 전수 점검(2026-11-04) + 확정 결함 5건 수정 d23 — **완료·실증**(2026-11-05)

사용자 지시 「모든 기능들에 대해 다양한 종류의 작업등 수행해서 정상 작동하는지 확인해봐」 → 「결함 5개 고쳐. Cpu 문제는 건드리지마.」.

- **전수 점검(dmsk8s 라이브 d19)**: 기준선 스냅숏 → 8 영역 병렬(인증·계정·보안 / 인벤토리·k8s·노드·정책 / scan·사용량 / sync /
  rm / 배치 / 결과물·요청 삭제·지표 / 포탈 UI 데스크톱·390px·이름·IP 입구) + FAIL 마다 독립 반박 재현 → 운영(유지보수·드레인·빌드 →
  레지스트리 → 잡 이미지 릴리스 → 원복, 로그인 감속) → 엔드포인트 138·화면 35 전수 대조 → 보충(제어면 릴리스 d22 → d19 원복 포함) →
  원복·흔적 0 확인. 판정 490행(원시 약 1,350건): PASS 464 · FAIL 12 · BLOCKED 2 · SKIP 10. 데이터 손상·권한 우회 0. 시험 데이터·요청·
  계정 0, 설정 = 기준선, 이미지 d19, post-boot PASS=103 FAIL=0. 보고서·하네스는 세션 job tmp `fv/`(REPORT.md·coverage.md).
- **확정 결함 5건 → 수정(소스 7434e1c5)**:
  1. 없는 `/api/*` GET 이 200 index.html — SPA 폴백을 `/api` 에 매치하지 않는 라우트로(404 JSON, 다른 메서드 전용 경로 405 JSON+Allow),
     `redirect_slashes=False`(끝 슬래시 /api 가 HTTPS 엣지 뒤에서 `http://` 로 307 되던 부작용 차단).
  2. 보호 화면이 `/me` 5xx·네트워크 오류에도 /login — 401 일 때만 /login, 그 밖은 제자리 「세션 확인에 실패했습니다」+「다시 시도」(재시도 중
     같은 버튼·포커스 유지), `/me` 만 5xx·네트워크 2회 짧은 재시도. 구현 중 세션 만료 시 `/me` 무한 재조회 루프(401 이벤트가 진행 중 me
     조회를 취소·재조회)를 찾아 함께 수정(`cancelRefetch: false`), Home 대기 판정 `isPending`.
  3. 390px 표 버튼·칸 음절 단위 줄바꿈 — 노드·감사·denylist·릴리스 이력에 `whitespace-nowrap`, e2e 03 375 점검에 세 화면.
  4. vcjob 시한 초과가 Failed — 라이브 Volcano 는 PodFailed→AbortJob 뒤 `{phase: Aborted}`(사유 없음)만 남긴다: 끝난 vcjob 이면 task
     파드(`volcano.sh/job-name`)의 DeadlineExceeded 로 TIMED_OUT(조회 실패는 poll_failed). 테스트 가짜를 라이브 모양으로.
  5. 미리보기 시한이 사전 점검 파드에도 걸려 preflight_failed — preflight·exec_preflight 는 고정 `PREFLIGHT_DEADLINE_SECONDS`(600),
     시한 초과는 TimedOut `preflight_timed_out`·`execution_recheck_timed_out`(양쪽 등록), 상태 설명·결과 배너·정책 화면 문구.
  리뷰 2라운드(지적 7 → 전부 반영, 2회차 0). 백엔드 5600 passed / 15 skipped, vitest 1858, tsc, e2e 32, dist 외부 URL 0.
- **배포**: 포탈 빌드 d23(3종, 225초) → 릴리스 dms-agent → dms-api → dms-controller → job-image 전부 Applied(live == 동봉 d23),
  오버레이 bump 590cd4ab(guard exit 0). d20~d22 는 점검 시험 빌드(원복·삭제).
- **라이브 재실증(d23)**: 검증 70행 + 독립 재확인 11행 모두 PASS — #1 두 입구에서 404/405 JSON·Location 없음·SPA 딥링크 그대로, #2 `/me`
  500·503·abort → 1.9초 뒤 제자리 패널(호출 1+2), 401 → /login(루프 없음), #3 390·375px 노드·감사·denylist 한 줄·페이지 넘침 없음,
  #4 dsync 실행 시한 2·5초 → 잡 TimedOut(vcjob Aborted + 파드 DeadlineExceeded), #5 미리보기 시한 3초 → preflight 는 600초 시한으로 성공,
  잡 TimedOut `preview_timed_out`. 기본 정책 scan·사용자 PV sync 정상(d23 잡 이미지). 정책은 바꾼 뒤 7~8초 안에 원복(비메타 차이 0).
- **남은 것(이번 범위 밖)**: 실행 시한 초과의 사유 코드가 여전히 `execution_failed`(stepper 실행 TimedOut 분기 — 그동안 도달 불가였다;
  화면 제목은 「시간이 초과」인데 사유 줄은 「실행에 실패」); 로그아웃 뒤 옛 서명 쿠키 재사용(설계 한계 — BACKLOG 보강 권고);
  테스트베드 워커 CPU(2 vCPU 에 DMS 외 requests 가 700m 를 넘으면 잡이 Volcano 에서 무기한 대기 — 사용자 지시로 손대지 않음).

### ✅ 옛 dmsk8s 레지스트리 pkg-01:5001 삭제 — testbed IaC 은퇴 목록·노드 신뢰 정리·DMS 헬퍼·문서 — **완료·실증**(2026-11-04)

사용자 결정(2026-11-04) 「이미지 레지스트리 dms-k8s 의 예전 방식 (5001) 은 삭제해도 돼. 되돌릴일 없을거야. 관련 스크립트들도 모두 정리해.」.
옛 방식 = pkg-01 의 두 번째 docker 레지스트리 `registry-k8s`(registry:2, 호스트 :5001, 데이터 `/opt/dms-registry-k8s` 3.0G — d1~d18·k0~k4). 2026-11-03
④ 앱 전환(d19)부터 k8s 테스트베드의 이미지는 전부 클러스터 내 레지스트리 `10.112.0.50:5000`(데이터 호스트 NFS `/dms_shared/dms/registry`)에 있고
:5001 은 되돌림용으로만 남아 있었다. **ssc 레지스트리 `registry`(:5000, `/opt/dms-registry`)는 건드리지 않는다.** DMS 코드·매니페스트 동작은 바뀌지
않는다(헬퍼 스크립트·주석·문서·테스트).

- **조사(읽기만)**: dmsk8s 의 워크로드·ConfigMap·DMS 설정·testbed 역할 어디도 pkg-01:5000/:5001 을 참조하지 않는다(애드온 역할은 상류,
  DMS 애드온·빌더는 클러스터 내); 노드 캐시에 남은 것은 k8s-cp1 의 `pkg-01:5001/dms:d17` 하나. 릴리스 이력의 `pkg-01:5001/…` 행(86)은 기록으로 남고
  다시 읽히지 않는다. d18 3종은 클러스터 내에 같은 digest 로 있다. 노드 신뢰는 두 파일 — `50-dms-registry.conf`(클러스터 내, 유지)와
  `99-pkg-01-insecure.conf`(pkg-01:5000 + :5001). 살아 있는 객체(파드·deploy·ds·sts·job·cronjob·vcjob·ConfigMap)의 참조는 0 이지만 **롤아웃
  이력은 옛 :5001 이미지를 가리킨다**(리뷰 r1) — dms-api·dms-controller 의 0-replica ReplicaSet 11개(d17·d18)와 dms-agent ControllerRevision
  9개(rev 14~22 = d10~d18). 쓰이지는 않지만 `kubectl rollout undo`(·`--to-revision`)로 그 리비전에 돌아가면 이미지도 신뢰도 없어
  ImagePullBackOff 다 — 되돌림은 포탈 릴리스로만 한다(deploy/README §2e-6; 코드·문서 어디도 rollout undo 를 쓰지 않는다).
- **testbed IaC**(`~/dms-dev/testbed` — 상세 그 저장소 `docs/ARCHITECTURE.md` §15.1·§19.6): `group_vars/all.yml` 의 `registry_instances` 에서
  `registry-k8s` 를 빼 `registry_instances_retired` 로 — `roles/registry_server` 가 활성 레지스트리 `/v2/` 확인 **뒤** 컨테이너 `docker rm -f` → 데이터
  디렉터리 삭제(멱등). **fail-closed 가드**(무엇이든 바꾸기 전에 목록 전체): 은퇴 이름·포트가 활성 것이면, data_dir 이 정확히 `/opt/dms-registry-<접미>`
  가 아니거나 활성 data_dir 과 같음·포함·하위면 거부; 제거 때 그 이름의 컨테이너가 `<data_dir>:/var/lib/registry` 를 붙인 그 레지스트리가 아니면,
  다른 컨테이너가 data_dir 을 붙이고 있으면, docker 가 「no such object」 외 오류면(모름) 거부. `scripts/tbinfo.py registries` 가 같은 규칙으로 스크립트에
  목록을 준다. `group_vars/dmsk8s.yml` `crio_insecure_registries: []`(키 유지) — `roles/crio` 가 빈 목록이면 `99-pkg-01-insecure.conf` 를 **지우고**
  reload(템플릿만 건너뛰면 옛 파일이 남아 신뢰가 이어졌다; ssc 는 렌더 바이트 동일), 신뢰 태스크에 태그 `crio_trust`. `dms_build_registry` 는
  `10.112.0.50:5000`(아무것도 읽지 않는 기록값이라고 주석). validate: pkg-01 레지스트리 검사가 활성만(하드코딩 5000/5001 → `tb registries`), 은퇴는
  부정 검사 3종(포트 리스너 없음 = curl 000 + `ss` 빈 출력, 컨테이너 없음, data_dir 없음 — ssh·sudo·docker 실패는 PASS 가 아니라 FAIL), 빈 신뢰 목록
  = 파일 부재; post-boot: 활성 포트 up·은퇴 포트 down. 주석·문서: Makefile help, `playbooks/registry.yml`, tfvars(plan 무변경), `coredns-node-hosts.sh`,
  README(레지스트리 표·시드 절 — dmsk8s 는 pkg-01 에 아무것도 없고 새 공용 디렉터리면 DMS §2e-4·§13-2), ARCHITECTURE §1 그림·§10·§15.1·§16·§17·
  §18·§21·§19.6. 노드 `/etc/hosts`·CoreDNS 블록의 pkg-01 줄은 공용 규칙이라 그대로(무해).
- **DMS**(이 저장소): `deploy/docker/registry-setup.sh` — dmsk8s 기본 신뢰 목록을 없앴다(`nodes --context dmsk8s` 는 `REGISTRIES` 없이 exit 2 —
  그 클러스터의 신뢰는 testbed crio 롤 몫), 머리 주석·레지스트리 표에서 `registry-k8s` 제거; 오버레이 testbed-k8s 주석(`kustomization.yaml`·
  `patch-config.yaml`·`values.env` — 되돌림용 문구 → 삭제), `deploy/render.sh` 빌더 미러 주석, `deploy/shared-storage-probe.py` 도움말 예시,
  `CLAUDE.md`·`docs/ARCHITECTURE.md` 불변식의 테스트베드 레지스트리 문구; 테스트 — `test_platform_registry_contract.py`(external `pkg-01:5001` 분기·전환 전
  skip 테스트 2개 제거 → in-cluster 단언 + 「values 만 external 로 바꾸면 렌더 거부」·「빌더만 다른 레지스트리면 렌더 거부」를 커밋된 오버레이에 직접),
  `test_shared_storage_probe.py`·`test_testbed_k8s_overlay.py` 예시·주석. `deploy/README.md`: 「테스트베드는 둘이다」·사실 표, §0(레지스트리 표·
  dmsk8s 헬퍼 줄 → testbed crio 롤·Verify), §2e-5 「걷어내기」·§2e-6 되돌림(external 로 돌아갈 곳 없음 — 옛 태그는 클러스터 내에 있는 d18·d19 만,
  d18 이하는 동봉 매니페스트가 `pkg-01:5001` 스탬프라 배지 「모름」; `kubectl rollout undo` 금지 — 리뷰 r1), §8 빌더 미러·§10 선행 컴포넌트 문구,
  §13-1(노드 신뢰·pkg-01), §13-2(옛 :5001 `d0` 복사 → 클러스터 내 레지스트리·새 공용 디렉터리면 §2e-4; 리뷰 r1 — 같은 공용 디렉터리라도 k8s-cp1 을
  새로 만들었으면 S4 전에 레지스트리 자기 이미지 S0-5 반입을 다시; 리뷰 r2 — 새 공용 디렉터리의 첫 부트스트랩 빌드 노드 testbed k8s-w1 에는 podman 이
  없다(실측, IaC 는 skopeo 만 — pkg-01 은 ClusterIP 에 닿지 않아 대신 못 한다) → 빌드 전 `sudo dnf install -y podman`; testbed README·§17(5) 도 같게). 레지스트리 자체(Distribution)의 debug 포트 `:5001` 언급은 무관해 그대로.
- **오프라인 검증**(라이브 무변경): 가드 26/26(`tbinfo.py registries` 와 역할의 가드 태스크를 그대로 꺼내 localhost 에서 같은 17 변형 — 이름·포트·
  data_dir 의 같음/포함/하위/상대/끝 `/`/`/opt`·활성 data_dir 이 은퇴 것 아래·이름/data_dir 끝·안 개행 — 판정 일치. 리뷰 r1: 역할의 `match`
  는 Python `re.match` 라 `$` 가 끝 개행 하나 앞에서도 맞아 `"registry\n"` 이 이름 규칙을 통과했다(→ 역할 `\Z`, tbinfo `fullmatch` — 그때도
  ssc 컨테이너는 제거 직전 바인드 검사가 막았다); 제거 직전 거부 9 — docker 29·옛 「No such object」 둘 다
  부재로, 데몬 불통은 모름, 같은 이름의 다른 컨테이너·바인드 없음·다른 컨테이너의 마운트(하위·정확히) 거부, 이웃 이름 `/opt/dms-registry-k8s2` 는 통과);
  `bash -n`·`py_compile`·shellcheck 새 경고 0·`--syntax-check`; 진짜 저장소에서 `make registry ANSIBLE='ansible-playbook --check --diff'` — pkg-01
  changed 2(`docker rm -f registry-k8s` echo + `/opt/dms-registry-k8s` directory → absent), `registry` :5000 무변경; `baseline --check --diff
  --limit dmsk8s --tags crio_trust` — 3노드 각 changed 2(99 파일 absent + Reload crio 핸들러), `--limit dms` 0; 태그 없는 전 VM `baseline --check` — dmsk8s
  각 2(같은 둘)·dms-* 각 1(기존 CRI-O apt 키 오탐)·pkg-01 0; `terraform plan` No changes(리뷰 r1 수정 뒤 가드·`--syntax-check`·registry·crio_trust
  `--check`·plan 을 다시 돌려 같은 결과). `READ_ONLY=1 validate`(적용 전 라이브): 전 PASS=284 FAIL=0 →
  후 PASS=279 FAIL=6 — 빠진 PASS 5(:5001 /v2/·`registry-k8s` 컨테이너·3노드 「trusts exactly pkg-01:5000 pkg-01:5001」), 새 FAIL 6 은 전부 「아직
  적용 안 함」(은퇴 3종 + 3노드 99 파일 존재), 나머지 줄 동일; post-boot 103/0 → 102/1(은퇴 :5001 응답). 적용 뒤 기대 validate PASS=285 FAIL=0,
  post-boot PASS=103 FAIL=0.
- **라이브 적용·실증**(2026-11-04 09:3x–09:42 KST, 런북 job tmp `r5001/apply-runbook.md`): 사전 기록 — :5001 카탈로그 dms·dms-agent·
  dms-mpifileutils(d1~d18 + k0~k4), dmsk8s 라이브 참조 0, `make registry --check` changed=2. `make registry` → pkg-01 changed=2(컨테이너
  `registry-k8s` 삭제 + `/opt/dms-registry-k8s` 삭제), 다시 돌려 changed=0(멱등); :5001 연결 거부(curl 000/exit 7)·리스너 없음, pkg-01 `/` 67% → 59%;
  ssc `:5000` 카탈로그 전과 같음·`dms:d194` 매니페스트 200·dms-w1 `crictl pull pkg-01:5000/dms-agent:d192` 정상·Ceph HEALTH_OK.
  `baseline --limit dmsk8s --tags crio_trust` → 3노드 각 changed=2(99 파일 삭제 + Reload crio), 다시 0; `50-dms-registry.conf` 그대로·crio active,
  k8s-w2 `crictl pull 10.112.0.50:5000/dms:d19` 정상, 비정상 파드 0, ssc 4노드 check changed=0. k8s-cp1 `crictl rmi pkg-01:5001/dms:d17`(09:41) → 노드
  5001 이미지 0. `make validate` **PASS=285 FAIL=0**(퇴역 검사 3 + dmsk8s 3노드 「no 99-pkg-01-insecure.conf」 PASS), `make post-boot-check` **PASS=103
  FAIL=0**(「retired registry registry-k8s :5001 stays down」). 데이터 되돌림은 없다(사용자 결정) — IaC·신뢰 되돌림은 git.

### ✅ 테스트베드 정식 구축 — 입구 영구 컷오버(호스트 edge haproxy = Avi VS-443 흉내 → Envoy cp1:443 PROXY v2, MetalLB 유지)·pkg-01 journald 상한 — **완료·실증**(2026-11-04)

사용자 지시 「이번 업데이트된 구현들의 실증을 위해 테스트베드 IaC 및 저장소 수정해도 돼. 임시로 구축말고 제대로 구축해줘.」. 설계 v6 #5·4-3·10 T8
「컷오버(MetalLB 유지)」, deploy/README §2g-4·§2g-7. **DMS 코드·매니페스트는 바꾸지 않았다**(문서·오버레이 주석만) — testbed 저장소(`~/dms-dev/testbed`)
IaC 가 본체다(상세·게이트: 그 저장소 `docs/ARCHITECTURE.md` §15.3·§15.5·§13·§19.5, `README.md` 「Portal edge」).

- **호스트 edge**(T8 의 손 haproxy → 관리 서비스): 배포판 haproxy(`apt`, Ubuntu 24.04 — 2.8 LTS) + group_vars 에서 생성한 `/etc/haproxy/dms-testbed-edge.cfg`
  (`scripts/host-edge.sh render|check|install|config|status`, `make host-edge`·`host-edge-check`) + drop-in `CONFIG=` — 패키지 conffile 은 그대로. Avi VS-443 값
  그대로(2g-4): L4 TCP, 풀 = `dmsk8s_cp` 전부 :443, `send-proxy-v2`, TCP 체크 5초(3/2), client/server 3600초, `leastconn`, 로그 journald. 받는 주소: **VIP-A
  `10.10.10.1:443`**(호스트 dmsbr0 주소 — libvirt 가 소유해 만들고 지울 주소가 없고, 모든 VM 에서 on-link, 호스트에서 붙어도 PROXY 출발지가 루프백이 아니다;
  :443 미사용·MetalLB 풀·공인 VIP·노드·외부 주소와 겹치지 않음을 `tbinfo.py` 가 검사) + 외부 `192.168.75.215:8443`·`100.65.195.106:8443`(옛 DNAT 와 같은
  주소·포트 — 사용자 안내 그대로, 원 IP = 실제 PC). 바인드는 `transparent`(부팅 때 dmsbr0·Tailscale 주소보다 먼저 떠도 bind — 없으면 haproxy 가 죽는 것을 실측).
  설정이 바뀌면 reload(배포판 ExecReload = `haproxy -c` 뒤 USR2 — 맺은 스트림은 끊기지 않는다), `haproxy -c` 통과분만 설치.
- **스위치** `portal_edge: gateway | ingress`(testbed `group_vars/dmsk8s.yml`, 기본 gateway): host-net 의 :8443 DNAT(ingress 에만)·호스트 `/etc/hosts` 의
  `dms-k8s.local`(gateway → VIP-A, ingress → 10.20.20.101)·haproxy frontend(gateway 에만)를 함께 정한다. 전환 `make host-edge host-net host-hosts`, 되돌림
  `portal_edge: ingress` + `make host-net host-hosts host-edge` — 세 스크립트가 틀린 순서를 아무것도 바꾸기 전에 거부한다(리스너 없는 포트의 DNAT 제거 /
  리스너 없는 VIP-A 로 이름 이동 / DNAT 없는 포트·아직 이름이 가리키는 VIP-A 의 frontend 제거). MetalLB VIP·ingress-nginx·`10.20.20.101` 라우트는 두 모드
  모두 그대로(되돌림용 — validate 가 늘 readyz 200 을 본다). 인증서 SAN 변경 없음(클라이언트는 이름으로 붙는다).
- **host-net 정리**(T8 이 짚은 두 함정): 릴레이·VIP·외부 주소를 스크립트 기본값에서 group_vars 로 옮겨 `make host-net` 이 `/etc/default/dms-testbed-hostnet`
  을 **생성**하고 스크립트가 직접 읽는다(유닛 `EnvironmentFile` 제거 — 손 `status`·validate 가 설정과 다른 기본값으로 검사하던 것 해소). 유닛에 `ExecReload`,
  `up` 은 설정에 없는 태그 규칙·옛 VIP 라우트를 prune 한 뒤 빠진 것만 더하는 수렴이라 `make host-net` 이 restart 대신 reload — ssc 릴레이가 한 순간도 빠지지
  않는다(T8 의 손 `iptables -D` 를 대신). ssc 규칙은 옛 기본값과 바이트 동일(오프라인 대조).
- **pkg-01 journald 상한**: `HEALTH_WARN MON_DISK_LOW`(루트 사용 가능 28% < 30%)의 원인 = 영구 저널 2.4 GiB(journald 기본 상한 10%·최대 4 GiB). testbed
  `group_vars/pkg.yml` `journald_system_max_use: "512M"` → `roles/common` drop-in + 재시작·vacuum 핸들러(pkg-01 만 — 클러스터 VM 은 임계에서 멀다),
  `--limit pkg-01 --tags journald` 로 단독 적용.
- **검증(validate·post-boot)**: gateway 클러스터에 「host portal edge」 절 — haproxy active·enabled·이 설정으로 실행·설치본 = 생성·bind 마다 LISTEN·백엔드
  UP, 릴레이 포트에 DNAT 없음, Gateway `dms/dms-portal` Programmed, 마스터마다 Envoy :443, **PROXY 강제**(PROXY 없는 직접 TLS 가 curl 35/56), VIP-A·외부
  주소마다 :8443 경유 readyz 200, 되돌림 VIP readyz 200; pkg-01 루트 ≥ 30%·journald drop-in·저널 크기; post-boot 에 haproxy·Gateway·VIP-A readyz·
  `envoy-gateway-system`.
- **오프라인 게이트**(라이브 무변경): 가짜 iptables/ip/ss/sudo/systemctl(라이브 `iptables -S` 시드) — host-net 26/26(ssc 규칙 바이트 동일, 라이브 테이블에서
  :8443 DNAT 4줄만 prune, 되돌림 복원, 가드·reload), host-edge 12/12(restart/reload/no-op·되돌림 가드 2종·apt·`haproxy -c` 실패 시 미설치), host-hosts
  가드 4/4; 버리는 Ubuntu 24.04 컨테이너의 배포판 haproxy `2.8.30-0ubuntu0.24.04.2` 로 생성 설정 `haproxy -c` valid; 생성 설정을 예비 포트로 잠깐 띄워
  실제 Envoy 로 `/readyz` 200·접근 로그 client_ip = PROXY 출발지·위조 XFF 무시·k8s-w2 경유 200, PROXY 없는 직접 TLS curl 35; shellcheck 새 경고 0;
  baseline `--check --diff` 전 VM — pkg-01 journald 3건만(dmsk8s 0, dms-* 의 1 은 기존 CRI-O 키 오탐); `READ_ONLY=1 validate` — ssc 검사 불변, 새 FAIL 은
  전부 「아직 적용 안 함」.
- **리뷰 r1 반영**(같은 날, 라이브 적용 전): ① 전환 가드가 리스너만 보던 것 → `host-edge.sh ready <클러스터> <주소>…`(frontend 열림 + 리스너 + 그 주소가
  **호스트 것**(transparent bind 는 없는 주소에도 LISTEN) + **헬스체크를 통과한 백엔드 UP** — 갓 시작한 haproxy 는 안 잰 서버를 `UP 1/3`·INI 로 보여 판정까지
  기다린다)을 host-net·host-hosts 가 옮기기 전에 본다; 되돌림은 edge 가 아직 응답하면 MetalLB VIP :443 TCP 응답을 본다(Ingress 삭제 뒤의 되돌림 방지).
  `status` 도 CSV 머리로 열을 찾는다(옛 awk `$3` 은 `UP 1/3` 을 UP 으로 읽었다). ② `portal_edge_vip` 는 호스트 dmsbr0 주소(`host_nfs_server`)만(tbinfo).
  ③ **V4 흉내**: cp1 :443 은 edge(10.10.10.1)에서만 — testbed `dms_envoy_443_sources` → `roles/common` nft 테이블 `inet dms_testbed_cp443` + oneshot
  유닛(kubelet 전, kube-proxy·Cilium 무간섭, nftables.service 는 끈 채); validate 가 워커 → cp1:443 직접 = curl 7 을 본다(적용 전 실측 curl 35 = VM 이
  PROXY 위조 가능). ④ 첫 설치가 중간에 죽어 haproxy 가 패키지 기본 설정에 남으면 `config` 가 reload 가 아니라 restart(다른 `-f`). ⑤ 바이너리 없음 메시지가
  삼켜지던 것. ⑥ validate journald = **보관** 파일 ≤ 상한(활성 파일은 상한 밖 — journald vacuum 이 세지 않는다), 512M ≈ 3일 보존(≈150 MB/일). ⑦ 새로
  짓는 중(DMS 네임스페이스 없음)엔 edge 백엔드 DOWN 이 INFO, README §13-6b(Gateway) 추가. ⑧ 되돌림 순서 주석·문서 정리. 오프라인: host-edge 27/27·
  host-net 30/30·host-hosts 11/11(진짜 haproxy 2.8.30 관리 소켓), tbinfo 7/7, nft 13/13(버리는 컨테이너), `--check --tags cp443` 전 VM(k8s-cp1 3건만),
  READ_ONLY validate — ssc 검사 불변.
- **리뷰 r2 반영**(같은 날, 라이브 적용 전): ① haproxy 는 헬스체크가 도는 순간의 `check_status` 를 직전 결과에 `* ` 를 붙여 보인다(`* L4OK` — 2.8 패키지
  management.txt §9.1 36번) — `ready` 가 그 순간 건강한 edge 를 「NOT ready … UP/*_L4OK」 로 거부할 수 있던 것을 `servers()` 가 표시를 떼도록 고침(진짜
  2.8.30 CSV 에 그 값만 고정한 소켓으로 재현·수정 확인, r1 스크립트 = 대조군). ② `config` 첫 설치는 diff 대신 설치하는 설정 전문을 출력. ③ 런북: 맨 위·4·5
  단계 끝에 **긴급 되돌림 한 줄**(`portal_edge: ingress` + `make host-net host-hosts host-edge` — 거부하는 첫 타깃에서 멈추고 거부는 무변경), 2b 되돌림을
  붙여 넣을 수 있는 명령으로, 「haproxy 손으로 걷기」 문단 → IaC 가 관리(edge 를 거둘 때 = `portal_edge:` 줄 삭제 → 빈 설정, 검사 건너뜀 — 오프라인 확인),
  저널 크기 확인을 validate 와 같은 셈(할당 바이트 + `*.journal~`)으로, #56(pkg-01:5001 정리)과 겹치는 validate 2줄·커밋 범위 확인(`precommit-check.sh`)·새
  파일 3개 `git add --` 를 명시. testbed `docs/ARCHITECTURE.md` §15.5(패키지·검증)·§19.5, `README.md` 「Portal edge」 동기화. 오프라인: host-edge 34/34.
- **라이브 적용·실증**(2026-11-04 08:16–08:21 KST, 런북 job tmp `edge/apply-runbook.md` 순서 그대로 — 되돌림 없음):
  ① pkg-01 journald(`--limit pkg-01 --tags journald`): 영구 저널 2.4 GiB → 471 MiB, 루트 사용 72% → 67%, Ceph `MON_DISK_LOW` 해소 → `HEALTH_OK`.
  ② cp1 :443 방화벽(`--limit k8s-cp1 --tags cp443`): `dms-testbed-cp443` active·enabled — k8s-w2 → cp1:443 직접 = curl exit 7(TCP reset — 적용 전 35),
  호스트(edge 출발지) → cp1:443 = 35(PROXY 필수). ③ `make host-edge`: 배포판 haproxy `2.8.30-0ubuntu0.24.04.1`, bind `10.10.10.1:443`·`192.168.75.215:8443`·
  `100.65.195.106:8443`, 백엔드 k8s-cp1 UP, VIP-A 경유 readyz 200. ④ `make host-net`(reload): :8443 DNAT **4줄만** prune — ssc DNAT 6줄·VIP 라우트·
  FORWARD 그대로; :8443 경유 readyz 200(외부 주소 둘 다), Envoy 접근 로그 `client_ip` = `192.168.75.215`·`100.65.195.106`(peer `10.10.10.1` — 원 IP
  보존; 옛 DNAT+MASQUERADE 경로는 `10.10.10.1` 이었다), ssc `dms.local` 200. ⑤ `make host-hosts`: 바뀐 줄은 `dms-k8s.local` 하나(10.20.20.101 →
  10.10.10.1), 이름으로 readyz 200, 봉인 로그인 alice → `/api/auth/me` actor=alice. 위조 XFF(k8s-w2 에서 `7.7.7.7`): `client_ip` = `10.10.10.22`,
  `x_forwarded_for` = `"7.7.7.7,10.10.10.22"`(위조값은 앞에만 — 판정에 쓰이지 않는다). ⑥ `make validate` **PASS=284 FAIL=0**(두 클러스터),
  `make post-boot-check` **PASS=103 FAIL=0**. 기록: testbed `docs/ARCHITECTURE.md` §19.5 결과 표. 남은 것은 BACKLOG ⑥(소크 뒤 Ingress·MetalLB 삭제 등).

### ✅ 테스트베드 워커 EL9(T-SE) — k8s-w1·w2 를 Rocky Linux 9.6(SELinux permissive)으로 재구성, SELinux 프로브·firewalld·EL9 고유 실측 — **완료·실증**(2026-11-03)

설계 v6 #49·10 「T-SE 세부」(4-2 「RHEL firewalld」·#48 SELinux 전제의 확인). **DMS 코드·매니페스트는 바꾸지 않았다** — testbed IaC 만. 이 세션은 testbed
저장소에 쓸 수 없어 작업 사본에서 고쳐 terraform·ansible 도 그 사본으로 돌렸고, 패치 + terraform state 를 넘긴다(job tmp `tse/handback.md` — 사용자
적용 대기, 그 전에는 진짜 저장소에서 terraform·ansible 금지 — BACKLOG).

- **IaC(작업 사본)**: terraform 노드별 `os`(ubuntu2404 | rocky9 — Rocky 9.6 vault GenericCloud sha256 고정, 기존 base 볼륨 그대로), cloud-init(wheel·
  짧은 hostname), EL 분기 roles(vault repo 고정·permissive·crashkernel 제거·authselect minimal·cri-o rpm 1.34.9 + container-selinux·kube rpm 1.34.6
  (exclude)·ceph-common 19.2.2 + EPEL `includepkgs` — 19.2.3+ 는 OPENSSL_3.4 를 요구해 9.6 에 못 올린다·kubelet `--resolv-conf=/etc/resolv.conf`(Rocky 엔
  systemd-resolved 경로가 없어 빠지면 파드가 하나도 뜨지 않는다)·firewalld 설치·꺼짐), `/dms_shared`(③) IaC, validate·post-boot 의 EL 점검(EL9 은
  `/proc/sys/fs/protected_*` 가 0600 이라 `sudo sysctl` 로 — 고친 결함 1). 회귀 게이트: 다른 노드 terraform plan 0·Ubuntu/ssc ansible check 의 changed
  수 동일.
- **실행(노드마다 drain → 꺼짐 확인 뒤 노드 삭제 → VM 재생성 → 프로비저닝 → 합류)**: drain → Ready w1 6분 54초·w2 6분 23초(apply → Ready 5분 52초·
  5분 40초, w1 은 빌드 노드라 그동안 포탈 빌드 공백). w2 전에 primary 를 cp1 로 promote(21초 — 그 순간 dms-controller 가 옛 rw 로 `Connection refused`
  exit 1 → 2회 재시작 뒤 자동 회복, 관찰만). 워커 메모리 2 → 3GiB(w1 빌드 중 MemoryPressure 실측 — BestEffort speaker 2회 축출; w2 는 재구성 apply 에
  함께, w1 은 도메인만 교체 — drain → Ready 1분 57초), cp1 2.5 → 4GiB(⑥ T8). 결과 노드: cp1 Ubuntu 24.04(4GiB), w1·w2 Rocky 9.6(커널
  5.14.0-570.17.1.el9_6, allocatable 2356Mi, permissive), CNPG 3/3.
- **검증**: validate-dmsk8s PASS=182·validate-dms PASS=132·post-boot PASS=96 — 남은 FAIL 1 은 셋 다 Ceph `HEALTH_WARN mon pkg-01 is low on available
  space`(재구성 전 기준선에도 있음, T-SE 무관). S2b PASS(cp1 no-selinux, w1·w2 permissive), S6 pull OK, S7 PASS(3노드), agent fresh·artifact base 쓰기 가능.
- **SE-1(S2b 판정 매트릭스 — ⑦ `selinux-probe.py` 와 같은 규칙의 일회용 프로브; 그때 그 파일은 미커밋이라 돌리지 않았다, 설치기 S2b 자체는 T9 ②)**:
  기준 PASS, `setenforce 1` → 그 노드만 FAIL 「enforcing」(w1·w2 각각), 원복 PASS, config 만 enforcing(w1) → FAIL 「재부팅하면 enforcing」, 원복 PASS(3노드).
  **enforcing 동안 실측 거부**: dms-agent(`container_t`)의 `/proc/1/mountinfo`(`init_t`)·CephFS 루트(`unlabeled_t` — `seclabel` 마운트지만 xattr 라벨
  없음) 읽기 → 그 회차 보고가 스토리지 전부 `Missing/not_a_mountpoint`·artifact base 쓰기 불가(보고 자체는 들어온다), `setenforce 0` 다음 회차에
  Ready. 설계 #48 「SELinux enforcing 금지」 전제(위험 26 「추정」)가 실측으로 확인됐다. permissive 기록(AVC — enforcing 이면 거부될 접근): dscan·dsync·
  nsync·agent → CephFS read/write/create/setattr, crio mounton `init_t`. NFS(`/k8s_pv`·`/dms_shared`) AVC 0, 빌드 파드(privileged)는 `spc_t`.
  CRI-O rpm 기본 `selinux = true`(Ubuntu 빌드는 false — 파드가 `container_t` 로 뜬다).
- **SE-2(EL9 고유)**: `crio config` 차이 — Ubuntu 대비 3줄(`storage_driver = "overlay"`·`overlay.mountopt=nodev,metacopy=on`·`selinux = true`; 앞 둘은
  빌드 노드의 skopeo → containers-common `/etc/containers/storage.conf` 때문, w2(Rocky) 와는 그 2줄만), registries.conf `short-name-mode = "enforcing"`,
  `fs.protected_regular` 1(Ubuntu 2), `/etc/crio/crio.conf.d/99-dms-pinned.conf` 경로 동작(설치 → pinned_images → 제거·reload). **firewalld 기본 public
  zone 을 켜면 노드 간 흐름이 깨진다**(w1, 기준선 7/7 PASS 뒤): Cilium(vxlan, KPR 꺼짐)이라 다른 노드 백엔드의 응답이 8472/udp 로 들어와 INPUT 에서
  막힌다 — 파드 DNS·호스트 → ClusterIP(레지스트리 000)·NodePort(000)·agent 보고(`Temporary failure in name resolution`) FAIL, 노드 안에서 끝나는
  외부 egress·VIP 는 산다. **설계 4-2 포트(10250/tcp·8472/udp·4240/tcp·4244/tcp·7946/tcp+udp·30000-32767/tcp)를 열면 7항목 모두 복구**(FORWARD
  정책은 바꿀 필요 없음). 최종은 firewalld 꺼짐(w1 의 `--permanent` 포트 7개는 `public.xml` 에 남아 다음에 켜면 열린 채 시작).
- **SE-3(잡·빌드)**: w1 scan(207 항목·72,193,764 B, preflight·launcher w1)·dsync(cephfs-third 안 — md5 목록·링크 동일)·nsync w1(Rocky) → w2(당시
  Ubuntu)(md5·소유/모드/종류 207항목 동일 — 교차 OS MPI), w2 scan·nsync w1 → w2(Rocky ↔ Rocky, launcher w2 — md5 201·메타데이터 207항목 동일),
  포탈 빌드 d20(210초, 빌드 파드 `spc_t` — **릴리스 안 함**, 라이브 d19 그대로).

### ✅ ⑦ 설치기 — 단계 설치기(S1~S13)·감지 상태 기계·SELinux·pull·마스터 프로브·인터넷 노드 push·S5 lock 대조·MetalLB/ingress 게이트 삭제 — **완료·실증**(2026-11-03, T9 — 라이브 테스트베드 no-op 확인·일회용 k3s 에서 감지 A·P·X·C·D=·D<·U)

설계 v6(`docs/history/specs/2026-11-03-dms-k8s-prod-design.md`) 8-1(S-1~S13·「S0 세부」·「S1 점검 항목」·「S2b 세부」·「S7 세부」·「S10 세부」·
「순서의 이유」)·8-2(감지 → 없으면 설치)·2-3(SELinux 전제)·7-5(images.lock·인터넷 노드 push·S5 대조)·9절 ⑦·10 T9. 런북은 `deploy/README.md` §2h
(설치기)·§2e-4(이미지 반입 — 인터넷 노드), 사이트 순서는 `deploy/overlays/prod/README.md`. **DMS 애플리케이션 코드는 바꾸지 않았다.**

- **`deploy/install.sh` 다시 씀**(옛 원커맨드 → 단계 설치기): `--context` **필수**(KUBE_CONTEXT·current-context 를 쓰지 않는다 — 모든 kubectl 은
  `kc`·`kcn` = `command kubectl --context "$CTX"`, 도우미에도 같은 값), `--site`·`--plan`(S1 + S2 확인 + S3, 무변경)·`--stage <S>`(재개)·`--only <S,…>`·
  `--upgrade-addon`·`--adopt`·`ADDON_INSTALL`. 단계: S1 preflight + 감지(FAIL·P·X 면 중단) → S2 `00-namespace.yaml` → `pg-secrets.py` ensure-app·
  init-secrets·4키·자리표시자·check → `PORTAL_TLS_SECRET`(존재·kubernetes.io/tls — 옛 `dms-portal-tls` 고정 게이트 대체) → S2b SELinux 프로브 → S3 계획 →
  S4 PriorityClass·레지스트리 SSA(`dms-install`)·http 시크릿(없을 때만, stdin)·rollout·port-forward `/v2/` 200 → S5 lock 대조 + DMS 3종 태그(다르면
  `push-images.sh --print` — **S4 에서 이어 왔으면 PAUSE exit 10**, 아니면 FAIL; 옛 호스트 podman push WARN 대체) → S6 pull 프로브 → S7 공용 스토리지
  (3 계속·1·4 정지) → S8 Gateway API·EG(감지대로, ns 먼저)·마스터 sysctl·platform/gateway·Programmed·마스터마다 :443 → S9 CNPG(ns 먼저·rollout·Cluster
  CR dry-run 9443 — 막 뜬 웹훅 재시도, 업그레이드면 직전 덤프·healthy 대기) → S9b Volcano(층이 있으면)·DMS 모양 vcjob dry-run → S10 pv-check →
  volumes → precheck → cluster → 900초 폴링 → contrast → backup → 첫 덤프 → S11 시크릿·앱 이미지 게이트·guard·migrate·`apply -k`·설정 rev 가 바뀌었는데
  파드가 그대로면 restart(`dms.io/install-config-rev` — dms-config·dms-secrets 의 resourceVersion, 값이 아니다)·마스터 사전 pull(WARN) → S12 verify →
  S13 체크리스트. **MetalLB·IngressClass nginx 게이트와 ingress-nginx 어노테이션을 지웠다**(설계 12 위험 19 해소). 종료 0·1·2·4·10, `STAGE:`·`RESULT:` 줄.
- **`deploy/install-detect.py`**(파트 A): 감지 상태 기계 — 항목 8개 × 코드 A·D=·D<·D>·C·C!·X·U·P(+ `-`·`?`) → action, `detect`·`plan`(층별 SSA server
  dry-run, 첫 설치의 ns·CRD 없음은 SKIP, 소비 층 dry-run 이 애드온의 최종 판정, C 층에는 dry-run 도 없음), 이번에 `vcjob` 모드(S9b — Volcano 판정 +
  vcjob dry-run 만)를 더했다(`vcjob_layer`·`consumer_verdicts` 로 plan 과 공유). CNPG 는 어디에든 있으면 두 번째를 설치하지 않는다(CRD 만·오퍼레이터만·
  둘 이상 = P). Volcano 스케줄러 탐지 라벨을 상류의 `app=volcano-scheduler` 로(옛 preflight 의 `app.kubernetes.io/name` 은 늘 빈 결과였다).
- **노드 프로브**(파트 B): `deploy/selinux-probe.py`(S2b — privileged 임시 DS, `imagePullPolicy: Never` 의 Cilium 이미지, `/sys/fs`·`/etc` 읽기 전용,
  enforcing·「재부팅하면 enforcing」(부팅 인자 > config) FAIL, selinuxfs 없음 + cgroup 보임 PASS, 읽기 오류 모름), `deploy/pull-probe.py`(S6 — 레지스트리
  이미지 `Always`, 120초, 오류 문구로 신뢰·서명·프록시·ClusterIP 경로 힌트; `--image --nodes` 로 S11 마스터 사전 pull). 둘 다 DMS 노드만 판정
  (`pg-path-probe.dms_nodes`). **`deploy/host-net-probe.sh`**(새 — 마스터 호스트 netns 일회용 Pod(hostNetwork·비 root·cap 없음): `--sysctl`
  `ip_unprivileged_port_start ≤ 443`(S8 — platform/gateway 전), `--listen` :443 LISTEN(S8 Programmed 뒤·S12 — sysctl 빠진 마스터는 Programmed·Ready
  인 채 조용히 빠진다, ⑥ 리뷰 1)).
- **인터넷 노드 반입·S5 대조**(파트 C): `deploy/push-images.sh`(POSIX sh — `--print`·`--run` 멱등·push 뒤 digest 재확인, `--only`·`--skip`, 프록시 env 가
  있는데 NO_PROXY 가 레지스트리를 덮지 않으면 아무것도 하지 않음 — Go httpproxy 규칙), `images-lock.py compare-registry`(매니페스트 본문 sha256 == lock,
  태그 없음·다른 digest FAIL — 태그 없는 lock digest 면 GC 위험 사유, 프록시 없이, 닿지 않음·익명 아님 모름). 설치기는 이 사이트가 받지 않는 줄을
  `--skip` 한다(C 인 CNPG 의 오퍼레이터, 레지스트리 없는 사이트의 레지스트리 자기 이미지).
- **preflight·verify**: 둘 다 컨텍스트 필수 — `--context` 또는 env `KUBE_CONTEXT`(플래그가 이긴다), 둘 다 없으면 exit 2(옛 「current-context 로
  돈다」 테스트를 거꾸로 고정; install.sh 는 플래그만 — 리뷰 2 문구 정정). preflight 는 K8s 1.34~1.36(밖이면
  FAIL)·control-plane 수·taint·클러스터 도메인·PSA(dms·envoy-gateway-system)·admission(VAP·웹훅·Kyverno/Gatekeeper)·`--render-env` 의 SHARED_DIR 형식·
  REGISTRY_CLUSTER_IP(ServiceCIDR 안·IPAddress parentRef/Service clusterIP 로 미사용·하단 정적 대역 min(max(16, n/16), 256))·감지 요약·설치 호스트
  도구·프록시·**노드 셸 체크리스트**(getenforce·SELINUX=·crio config 의 pinned_images·signature_policy·`crictl inspecti … pinned`·런타임 NO_PROXY·sysctl·
  findmnt -T). MetalLB·ingress-nginx 절은 지웠다. verify 는 CNPG·dms-pg-rw·여분 PV·Released·CronJob·경로 대조·레지스트리 Ready·clusterIP·라이브 태그
  (apiserver 서비스 프록시 GET)·Gateway Programmed·마스터 :443·readyz `--resolve`·「80 불가 = 정상」·애드온 요약·FAIL 이면 api·controller 로그의 EACCES
  → S2b 다시 안내(`--no-probes` 면 파드를 띄우지 않는다).
- **구현 중 잡은 결함**: port-forward 를 셸 함수(`kcn … &`)로 띄우면 `$!` 가 서브셸이라 kill 해도 kubectl 이 남았다(dmsk8s 실측 — 프로세스 1개 잔류) →
  `( exec kubectl --context … ) &` 로 고치고 테스트가 끝난 뒤 그 PID 가 없는지 본다. 테스트 심이 `echo "$*"` 로 jsonpath 의 `{"\n"}` 를 줄바꿈으로
  바꿔 「--context 없는 호출」로 잘못 보였다(dash echo) → printf.
- **문서**: deploy/README §2h(설치기 단계 표)·§2e-4(인터넷 노드 반입 런북 — 파트 C 초안)·§2d~§2g 의 「⑦ 전에는 손으로」 문구·머리 배너, prod README 를 단계
  흐름으로(0. 사이트 준비 — **SELinux 전제** 포함, 1~5 설치, 이미지 반입 요약, 선행조건 표), `values.env.example`·`images.lock` 머리 주석, ARCHITECTURE
  모듈 행 5·불변식 2(「모든 kubectl 에 --context」·「SSA manager dms-install, C 는 다시 설치하지 않는다」), BACKLOG ③④⑤⑥ 의 ⑦ 항목 해소 + 「⑦ 설치기 후속」.
- **테스트**: `tests/test_install_stages.py`(새 — 심 kubectl·가짜 도우미로 단계 순서, S4 PAUSE → `--stage S5` 재개, S5 lock·DMS 이미지 FAIL·모름,
  port-forward 잔류 없음, S7 0/3/1/4/2, external 대 in-cluster, 멱등 재실행 2회, `--plan` 무변경(세계 JSON 이 그대로), C 애드온 비적용·A 순서(ns 먼저),
  `--upgrade-addon`·`--adopt` 전달, 감지 FAIL·Volcano 층 없음 S1 정지, S2 TLS·S2b·S10 폴링 판정·첫 덤프 실패·S11 설정 재시작·가드 거부, 실제 render.sh
  (prod·testbed-k8s)와 이어서, preflight 의 새 점검), `test_check_scripts_context.py`(필수 `--context`·host-net-probe·설치기의 정적 「kubectl 다음 낱말」
  검사), `test_prod_k8s_overlay.py`(MetalLB·ingress 게이트 0건). 옛 `test_platform_registry_contract.py` 의 install.sh in-cluster 게이트 3건은 같은 단언을
  S5·S11 로 옮겼다. 파트 A·B·C 테스트(`test_install_detect`·`test_selinux_probe`·`test_pull_probe`·`test_push_images`·`test_images_lock`) 그대로.
- **테스트베드 실측(dmsk8s — 읽기 전용, 2026-11-03)**: `install.sh --context dmsk8s --site testbed-k8s --plan` → RESULT PASS(리뷰 2 뒤로는 preflight
  WARN 이 판정에 들어가 S1·RESULT WARN; S1 preflight WARN 1 = control-plane 1대, 감지 priorityclass·registry·gateway·db D= reapply · gateway-api·envoy-gateway·cnpg D= none · volcano C(v1.15.0) — 층 dry-run PASS 8·SKIP 4).
  `--only S5` → lock 6줄 OK + dms·dms-agent·dms-mpifileutils d19 있음 PASS, port-forward 잔류 0. `verify.sh --no-probes` PASS 13·WARN 0·FAIL 0, readyz 는
  MetalLB VIP(병행 중인 옛 입구) `--resolve dms-k8s.local:443:10.20.20.101` 200. 렌더는 작업 트리의 `deploy/.rendered` 를 건드리지 않게(T8 병행) 복사본에서.
- **설계와 다른 점**: (1) Volcano 층은 아직 없다 — 감지가 A 면 S1 이 멈춘다(lock 3줄·VOLCANO_VERSION 도 그 층과 함께 — BACKLOG). (2) S4 뒤 멈춤은
  「S5 대조가 실패할 때만」이다(재실행이 늘 멈추지 않게 — 멱등). (3) `--only`·`--plan` 의 S2 확인 모드·PAUSE 종료 코드 10·`VERIFY_NO_PROBES` 는 설계에 없던
  모양이다. (4) S10 폴링은 세 조건이 선 뒤에도 `spec.instances` 까지 기다리고(contrast 가 join 중인 PVC 를 모름으로 멈추지 않게) 시간 초과에 ≥ 1 이면
  WARN 이다. (5) 설정 해시는 값의 해시가 아니라 resourceVersion(비밀을 다루지 않는다). (6) S6·S2b 의 판정 범위는 DMS 노드(S6 는 설계 행에 범위가 없다).
  (7)(리뷰 1) S2 의 TLS 시크릿은 WARN — 쓰는 곳인 S8 이 멈춘다(설계 S2 판정 「—」). (8)(리뷰 1) S12 의 readyz 판정은 apiserver 서비스 프록시이고
  `--resolve` VIP 무응답은 WARN 이다(첫 설치에서 Avi VS-443 이 S13 몫이라 늘 FAIL 이었다).
- **리뷰 1 고침(2026-11-03)**: (high) 앱 태그를 올리면 S3·`--plan` 이 끝난 `dms-migrate` Job 의 `field is immutable` 로 늘 FAIL — 앱 층에서 그 줄만
  「S11 이 지우고 다시 만든다」 PASS(dmsk8s d19→d20 사본 `--plan` 실측 PASS). 재개 명령(`again`)이 `--upgrade-addon`·`--adopt`·판정 env 를 싣는다(빠지면
  재개한 S9 가 업그레이드 없이 rc 0). env `DMS_NS` 를 무시(`unset`)하고 도우미 세 곳·verify·preflight 에 `--namespace dms`. S4 가드는 레지스트리 층을
  적용할 때만·`--upgrade-addon registry` 면 allow-image, CNPG 업그레이드 가드는 Cluster 유무와 무관(덤프·healthy 만 Cluster 에 묶음), `--adopt db` 면
  S9 dry-run 도 `--force-conflicts`. 빈 `--stage`·`--only` 는 사용법 오류. 시크릿 읽기 실패는 모름(exit 4)이고 `--plan` 에서 FAIL 뒤 [OK] 를 찍지 않는다.
  애드온 ns 를 DMS 라벨로 SSA(끊겨도 다음 감지가 P 로 멈추지 않게), DMS 소유 EG·CNPG 를 적용하지 않을 때도 Ready·xDS 시크릿 확인(실패 certgen 은 지우고
  다시). S4 실패 사유에 파드 노드·FailedScheduling·ImagePullBackOff(S0-5 미반입), S2b 사용법 오류에 `SELINUX_PROBE_IMAGE`, S5 PAUSE 에 실제 태그,
  S12 FAIL 이어도 S13 체크리스트, S13 첫 관리자 부트스트랩(port-forward + 토큰 파일 — 출력·argv 없음). 문서: prod README §0(레지스트리 주소 먼저·Avi
  VS-443·kubelet 값·마스터 skopeo/crictl)·첫 관리자·CACERT, deploy/README §1(base 매니페스트를 고치라던 문단)·§2e-2·§2e-3(ImagePullBackOff)·§2e-5·§2h,
  build-and-push.sh 안내. 테스트: 단계별 멈춤 18건 표·S10 순서(사전 검사 → Cluster)·migrate 선삭제 순서, `tests/test_install_verify.py`(새 — verify.sh
  판정 표·EACCES 힌트·`--no-probes` 무파드, host-net-probe 의 sysctl·:443 판정과 /proc/net/tcp 정규식), preflight Volcano 미설치·PSA 비특권 FAIL,
  `test_install_detect` migrate immutable·DMS_NS.
- **리뷰 2 고침(2026-11-03)**: `--plan` 이 모름·FAIL 줄에 클러스터를 바꾸는 `--stage S1`·`--stage S2` 를 권했다 → `again()` 은 계획에서 늘 `--plan`,
  「계획 끝」의 실제 설치 명령은 FAIL·모름이 없을 때만 계획과 같은 플래그·env(`--adopt`·`ADDON_INSTALL`·`VALUES_ENV`)로; Volcano 층 게이트는 S1 이
  돈 실행에서 S3 가 다시 부르지 않는다(같은 FAIL 두 번·dry-run PASS 인 S3 FAIL·「계획 끝」 누락). preflight(S1)·verify(S12)의 `[WARN]` 을 세어
  STAGE·RESULT 를 WARN 으로(dmsk8s `--plan` 「WARN 1」인데 PASS 였다). 도우미 출력은 4칸 들여 쓰고 감지·계획의 기계 줄(DETECT·PLAN·RESULT)은 사람
  출력에서 뺀다 — 0열 `RESULT:` 는 설치기의 한 줄뿐(python 도우미는 `PYTHONUNBUFFERED=1` 로 진행 줄을 그대로 흘린다). 재개 줄에 S12 의 `CACERT`·
  `PORTAL_DOMAIN`·`PORTAL_VIP`·`VERIFY_NO_PROBES`. 가드 exit 3 안내를 층별로(애드온·플랫폼 = `--only <S> --upgrade-addon <x>`, 전역
  `ALLOW_IMAGE_CHANGE=1` 은 S11 앱 가드까지 연다), `--upgrade-addon <x>` 의 D= 재적용(lock digest 만 바뀜 — action reapply)도 그 층 가드를 연다.
  S5 는 lock 대조 PASS 면 lock push 블록을 다시 보이지 않고, 머리에 「같은 커밋의 저장소 루트에서」(push-images.sh 의 `lock:` 표시는 상대 경로),
  external TLS 레지스트리(`REGISTRY_TLS_VERIFY=true`)면 push 안내에 `--dest-tls-verify true`(대조·push 가 같은 규칙 `reg_tls` — 빈 값은 false:
  전에는 빈 값이면 대조만 https 였다). `--help` 는 머리 주석·exit 0. preflight 노드 체크리스트는 external 사이트에 `<REGISTRY_CLUSTER_IP>`·
  pinned_images·`crictl inspecti` 대신 사이트 REGISTRY 신뢰·서명 정책·런타임 NO_PROXY. 낡은 문구(「Volcano 층은 ⑦ 에서」·「install.sh 의 push」·
  「S1 자동 검사는 ⑦」·Volcano 「≥ v1.15」만). 문서: 렌더는 매 실행 처음에 다시 한다 — `deploy/.rendered` 손 편집은 적용되지 않는다(prod README·
  deploy/README §2g-2·§2h), 컨텍스트 규칙(install.sh·host-net-probe 는 플래그만, preflight·verify·python 도우미는 플래그 또는 env), `REGISTRY_TLS_VERIFY`
  (external TLS = push·대조만, 포탈은 http). 테스트: `test_install_stages`(계획의 다음 명령 3경우·계획 끝 줄의 플래그·env·S3 단독 Volcano 게이트·preflight/
  verify WARN·S12 재개 env·가드 안내 층별·`--upgrade-addon` reapply 가드 3층·S5 lock PASS·external TLS·0열 RESULT 하나·`--help`·preflight external
  체크리스트), `test_install_detect`(vcjob 모드 — volcano 만 판정·dry-run 실패 FAIL·X/P 면 dry-run 없이 FAIL).
- **T9 실증(2026-11-03)** — 증거는 메인 세션 job tmp(`b7/t9/` — 실행마다 `<행>.out`·kubectl 호출 기록 `<행>.k.log`, 저장소 밖):
  - **라이브 테스트베드(dmsk8s · testbed-k8s, d19 — 바뀌는 것이 없음을 확인)**: `--plan` rc 0·RESULT WARN(preflight 「control-plane 노드 1대」 — 단일
    control-plane 이면 정상, 감지 D=·C·층 dry-run PASS·SKIP). `--only` 로 S1(같은 WARN)·S2b·S5·S6·S7·S12 PASS. S4·S8·S9·S9b·S10 재실행 PASS — 그 단계가
    SSA 하는 15객체(PriorityClass·GatewayClass·레지스트리 Deployment·Service·Gateway·HTTPRoute·EnvoyProxy·ClientTrafficPolicy·Cluster `dms-pg`·CronJob·
    PV `dms-pgdata-01~05`)의 resourceVersion 이 전후 같다(SSA no-op). **S11 은 돌리지 않았다** — migrate Job 을 지우고 다시 만드는 단계라 no-op 이
    아니다(라이브 앱은 포탈 릴리스 몫).
  - **일회용 k3s(dmst9 — 호스트 컨테이너 k3s v1.34.12 서버·워커 + 레지스트리 컨테이너, 끝난 뒤 컨테이너·볼륨·kubeconfig 를 걷었다)**: 행 A1(빈
    클러스터 → 전부 A install)·A2(`ADDON_INSTALL=never` → 서드파티 A stop)·A3~A7(Volcano C 위 A 경로: 계획 → S2b 사용법 오류 rc 2(Cilium 없음 —
    `SELINUX_PROBE_IMAGE` 안내)·S7 FAIL(공용 마운트 없음)·`--only S8,S9,S9b` 실제 설치(ns 먼저·certgen·rollout·Cluster CR dry-run) → 재실행 D=)·C1(남의
    CNPG 1.30.1 → C reuse, Cluster CR dry-run 이 최종 판정)·CG(남의 Gateway API v1.6.1 → C, S8 은 EG·입구만)·D1(DMS CNPG 1.29 → D< keep →
    `--upgrade-addon cnpg` S9 → D=)·D2(1.28 + `--upgrade-addon cnpg` → 「minor 는 하나씩」 stop)·P1(라벨 없는 PriorityClass → P stop → `--adopt priorityclass` S4 `--force-conflicts`
    → D=)·P2(CNPG 오퍼레이터 2개)·P3(CRD 만 → `--adopt cnpg` S9)·P4(남이 쓰는 envoy-gateway-system)·X1(PriorityClass value 999)·X2(Gateway API
    v1.2.1)·X3(CNPG watch 에 dms 없음)·X4(마스터 sysctl > 443 → S8 FAIL)·U1(버전 모르는 EG → 계획 dry-run 통과 WARN) — 전부 기대(`test_install_detect`)
    대로. D>·C! 은 만들지 않았다(심 테스트만).
  - **T9 에서 고친 것**: ① SSA 충돌 안내가 늘 「채택은 --adopt 로만」이었다 — `--adopt` 는 감지 P 에만 듣는다. 이제 충돌한 field manager 이름
    (apiserver 문구 `conflict with "<m>"`)과 「손 수정이면 되돌리거나 `kubectl --context C apply --server-side --field-manager=dms-install
    --force-conflicts -k <층>` 로 덮어쓰고 같은 단계부터, 다른 도구면 소유자와 정리」를 보인다(P 만 `--adopt` 안내; 계획의 충돌 힌트도 같게) ② 감지
    WARN 이 감지를 처음 부른 단계에 붙었다(`--only S9` 가 envoy-gateway U 로 `STAGE: S9 WARN` — C1b) → 감지 WARN·FAIL·모름 줄에 항목(단계 코드 동작)을
    싣고, WARN 은 그 항목의 단계가 센다(S1 이면 S1, 뒤에 돌 단계면 그 단계, 이번 실행 밖이면 RESULT 에만) ③ `guard-images.sh` 의 로컬 렌더
    `kubectl kustomize` 만 `--context` 가 없었다 → 컨텍스트가 있으면 단다(불변식 「모든 kubectl 에 --context」, `test_check_scripts_context` 실행 검사)
    ④ 애드온이 stop·P 면 계획의 소비 층(platform/gateway·db-cluster)·앱 층이 날것의 `no matches for kind` 로 FAIL 이었고(A2·P4), P4 에서는 그 FAIL 이
    멀쩡한 gateway-api(D=)까지 「최종 판정 FAIL」로 번졌다 → vcjob 처럼 `skipped: <애드온> 판정 FAIL(<코드> stop) — <종류> 는 확인하지 않는다`
    ⑤ 문서: 입구 제거·되돌림 순서(platform/gateway → EG → Gateway API — GatewayClass 의 `gateway-exists-finalizer` 를 EG 가 걷는다, deploy/README
    §2g-6)·단일 control-plane 의 `--plan` RESULT WARN 은 정상(§2h). 테스트: `test_install_stages`(충돌 안내 2형·감지 WARN 자리 4경우·감지 FAIL 항목
    이름), `test_install_detect`(stop 의존 SKIP — P4·A2 모양·진짜 오류와 섞이면 FAIL·충돌 힌트 D=/P·관리자 이름 파싱), `test_check_scripts_context`·
    `test_guard_images_context`(렌더 호출의 `--context`).
- **남은 것**: Volcano 층, 새 사이트 DB import bootstrap, `kubectl-cnpg` 반입, T9 에서 만들지 않은 D>·C! 의 실클러스터 확인 — BACKLOG 「⑦ 설치기 후속」.

### ✅ ⑥ 입구(Envoy Gateway) — Gateway API v1.6.1 standard·EG v1.9.3 애드온 층·DMS Gateway/HTTPRoute/정책/EnvoyProxy·prod 의 Ingress·MetalLB 삭제 — **완료·실증**(2026-11-03, T8 — 테스트베드는 병행 설치·컷오버 시험 뒤 원복; 영구 컷오버는 testbed IaC)

설계 v6(`docs/history/specs/2026-11-03-dms-k8s-prod-design.md`) #5·#13~#15(「#14 EG 기본값 세부」)·#22·#42·2-1/2-2(Envoy·EG 컨트롤러 행)·4-2·4-3·
5 「원 IP 기록」·8-1(S0-5·S1·S8·S11·S12·「순서의 이유」)·8-2(Gateway API·EG 행)·9절 ⑥·10 T8·12 위험(#12·#15·#16·#19). 런북은 deploy/README §2g.
**DMS 애플리케이션 코드는 바꾸지 않았다** — 원 IP 를 읽는 쪽(`auth.client_ip` — XFF 마지막 항목 → 피어, X-Real-IP 무시)은 ② 에서 이미 이 계약에 맞췄다.

- **버전 선택(2026-11-03 GitHub releases 조회)**: EG **v1.9.3**(2026-10-26 — 1.9.x 최신; v1.10.0-rc.1 은 prerelease, v1.8 은 2026-11-08 EOL; 호환 표
  v1.9 = K8s 1.33~1.36·EOL 2027-02-14). Gateway API **v1.6.1** standard(1.6.x 최신은 v1.6.3) — EG v1.9.3 의 go.mod 가 v1.6.1 이고, v1.6.1 → v1.6.3 의
  standard CRD 차이는 bundle-version 어노테이션·설명문뿐(스키마 0 — diff), v1.7.0 은 EG v1.9 가 따르지 않는다. 상류는 두 매니페스트에 서명을
  싣지 않는다 — 무결성은 GitHub 자산 digest(서버 계산) == 받은 파일 sha256 으로 기록(`SOURCE`). 이미지 lock(linux/amd64 단일 매니페스트, `images-lock.py
  verify` PASS): `gateway:v1.9.3` ← `docker.io/envoyproxy/gateway:v1.9.3@sha256:83dee303…`(index `192fc9bd…` 의 amd64 — 컨트롤러·certgen Job·
  shutdown-manager 가 같은 이미지라 줄 하나), `envoy:distroless-v1.39.5` ← `…/envoy:distroless-v1.39.5@sha256:00c38048…`(index `9d84a586…` = EG v1.9.3
  `DefaultEnvoyProxyImage` 의 고정 digest 의 amd64 항목).
- **애드온 층 `deploy/addons/gateway-api/v1.6.1`**: 상류 `standard-install.yaml` 바이트 그대로(CRD 10개 + safe-upgrades VAP·Binding, 이미지 0) + 라벨
  층(managed-by `dms-install`·`dms.io/addon-version=v1.6.1`).
- **애드온 층 `deploy/addons/envoy-gateway/v1.9.3`**: 상류 `install.yaml` 바이트 그대로 + DMS 패치 층 — ① 어노테이션 `gateway.networking.k8s.io/
  bundle-version` 이 붙은 객체 전부 삭제(상류가 싣는 Gateway API **실험 채널** CRD 13개(x-k8s 3개 포함)·VAP — 원천은 standard 층; EG 컨트롤러는 없는
  CRD 의 watch 를 건너뛴다 — `controller.go` checkCRD), ② 이미지 `envoyproxy/gateway`(호스트 없음 = docker.io) → 자리표시자 → 렌더러가 lock digest ref
  (컨트롤러·certgen), ③ EG 설정 ConfigMap 을 DMS 판으로: shutdown-manager 이미지·**기본 EnvoyProxy 의 Envoy 이미지**(다른 Gateway 가 생겨도 docker.io
  로 가지 않게 하는 그물)·`runtimeFlags.enabled: [EnvoyProxyPatch]`(DMS EnvoyProxy 의 hostNetwork 패치가 이 플래그로만 들어간다 — 꺼지면 조용히 무시)·
  상류 ratelimit 이미지 줄 삭제(전역 rate limit 미사용), ④ 컨트롤러 워커 배치(required nodeAffinity control-plane `DoesNotExist`)·`dms-addon-critical`,
  ⑤ ns `envoy-gateway-system` PSA privileged(Envoy DaemonSet 이 거기 hostNetwork 로 뜬다), ⑥ 라벨(상류 Helm 을 덮음, selector 는 그대로).
- **플랫폼 층 `deploy/platform/gateway`**(DMS 소유): GatewayClass `dms` · Gateway `dms-portal`(ns dms, HTTPS 443 리스너 하나 — hostname·80·HSTS 없음,
  인증서 `dms-portal-tls`, `infrastructure.parametersRef` → EnvoyProxy — A·C 경로 공통) · EnvoyProxy `dms-portal`(DaemonSet control-plane + toleration,
  패치로 `hostNetwork: true`·`dnsPolicy: ClusterFirstWithHostNet`, `useListenerPortAsContainerPort: true`, Service ClusterIP, concurrency 2, 100m/256Mi..
  1/2Gi, maxSurge 0, envoy·shutdown-manager 이미지 명시, 접근 로그 JSON — `client_ip`(PROXY 출발지)·`peer`(직접 피어)·`x_forwarded_for`·쿼리 뺀 경로) ·
  HTTPRoute(/ → `dms-api:8080`, `timeouts.request: 3600s`) · ClientTrafficPolicy(`proxyProtocol.optional: false`, `earlyRequestHeaders.remove: [X-Real-IP]`,
  clientIPDetection 없음, `withUnderscoresAction: DropHeader` — 옛 nginx 처럼 헤더만 버린다; EG 기본은 요청 400) · BackendTrafficPolicy(`requestBuffer.limit:
  20Mi`). Envoy 는 비 root(65532) — 443 bind 는 마스터 sysctl `ip_unprivileged_port_start=443`(S0-5)이 전제다.
- **렌더러(`deploy/render.sh`)**: `versions.env` 에 `GATEWAY_API_VERSION`·`ENVOY_GATEWAY_VERSION`·`ENVOY_GATEWAY_IMAGE`·`ENVOY_PROXY_IMAGE`, 입구 층을
  늘 낸다(`GATEWAY:` 줄 S8 순서 — 기존 `PLATFORM:`/`ADDON:`/`PROBE:` 순서·`ADDON_LAYERS` 는 그대로). EG 설정은 base `patch-config.yaml` 의 두 자리표시자를
  글자 그대로 바꾼 사본(설정 문자열이라 kustomize images 가 못 닿는다 — 남으면 FAIL), EnvoyProxy·CTP·Gateway 는 JSON 패치(shutdown-manager 위치는 `test`
  op 로 확인). values(전부 선택): `GATEWAY_PROXY_PROTOCOL`(required 기본 | optional — stderr 알림, 컷오버 중에만), `PORTAL_TLS_SECRET`(기본
  dms-portal-tls, DNS-1123), `GATEWAY_ENVOY_{CPU,MEMORY}_REQUEST`(testbed-k8s 50m/128Mi — 설계 10). 버전·태그·자리표시자·lock 이 어긋나면 FAIL.
  `PORTAL_DOMAIN`·`PORTAL_VIP` 는 prod 필수 키로 두되 `render.env` 로만 낸다(인증서 SAN·S12 `--resolve`).
- **prod 오버레이**: `patch-ingress.yaml`·`patch-metallb.yaml` 을 지우고 `patch-remove-ingress.yaml`(`$patch: delete` — Ingress `dms-portal`·IPAddressPool
  `dms-public-pool`·L2Advertisement `dms-public-l2`). 공용 base 46·47 은 그대로(testbed-k8s 가 T8 동안 병행, main·dms-ssc 와 같은 파일). 지우지 않으면
  MetalLB CRD 가 없는 prod 에서 apply 가 `no matches for kind` 로 실패했다.
- **가드(`guard-images.sh`)**: `addons/envoy-gateway` 의 컨트롤러를 `envoy-gateway-system` 에서 읽어(⑤ 의 네임스페이스 추출 그대로) 이미지가 바뀌면 exit 3 +
  EG 업그레이드 순서 안내.
- **검증(오프라인)**: egctl v1.9.3(릴리스 바이너리 — 자산 digest 대조)의 `x translate` 로 렌더 결과를 xDS 로 번역 — 모든 정책·EnvoyProxy·Gateway·
  HTTPRoute Accepted, 리스너 `0.0.0.0:443`(포트 이동 없음), `envoy.filters.listener.proxy_protocol`(required — optional 렌더는
  `allowRequestsWithoutProxyProtocol: true`), early header mutation `remove: X-Real-IP`, `useRemoteAddress: true`·XFF 신뢰 홉·originalIpDetection 없음,
  버퍼 필터 `maxRequestBytes: 20971520`, 라우트 `timeout: 3600s`, 접근 로그 포매터(REQ_WITHOUT_QUERY 확장 자동). CR 6종은 CRD openAPIV3Schema 로
  알 수 없는 필드·enum·타입 대조(Strict field validation 과 같은 기준) PASS. dmsk8s server dry-run: Gateway API 층 전부 · EG 층의 CRD·클러스터 범위
  객체(ns 객체는 첫 설치 NotFound — ⑤ 와 같은 함정, 런북은 ns 먼저).
- **테스트**: 새 `tests/test_gateway_contract.py`(62 — 리뷰 1·2 포함) — 상류 바이트·버전/lock/디렉터리/라벨 맞물림·EG base(Gateway API 객체 0·자리표시자 이미지 4·외부
  참조 0·워커·PriorityClass·PSA·라벨/selector)·렌더(이미지 = 애드온 레지스트리 + lock digest·Always 0·EnvoyProxy 바인드 모양·자원(prod 100m/256Mi,
  testbed 50m/128Mi)·접근 로그·CTP 계약·Gateway 리스너·HTTPRoute 3600s·BTP 20Mi)·값 검사·셸 env 무시·versions/lock/base 불일치 FAIL·가드.
  `tests/test_prod_k8s_overlay.py` — 삭제 패치 모양, 앱 렌더에 Ingress·IPAddressPool·L2Advertisement·metallb 0건, 입구 층 렌더(PROXY 필수).
- **리뷰 1 반영(2026-11-03)**: ① EnvoyProxy `extraArgs: [--disable-hot-restart]` — EG v1.9.3 `BuildProxyArgs` 가 `--base-id` 를 넘기지 않아 hot restart
  추상 소켓(base-id 0, netns 단위)이 hostNetwork 의 cilium-envoy(`--base-id 0` — dmsk8s 에서 확인)와 겹쳐 늦게 뜬 쪽이 `errno=98` 로 죽는다(리뷰어가
  docker 로 재현). ② sysctl 이 빠진 마스터는 **조용히** 빠진다 — Envoy 는 LDS bind 실패를 NACK 만 하고 Ready, EG 의 Programmed 는 DaemonSet
  `NumberAvailable > 0` 만 본다(상류 status/gateway.go) — 문서의 「Programmed False·CrashLoop」 증상을 바로잡고 확인을 마스터마다 :443 으로, 고친 뒤
  Envoy 파드 재기동을 더했다(README §2g-2·2g-3·2g-5, BACKLOG ⑦ S12). ③ 「EG 가 root securityContext 를 정화·차단」은 사실이 아니다(상류
  `applySecurityContextOverrides` = 기본값 위 strategic merge) — 세 문서의 근거를 「설계 (i) 선택·S1 범위·계약 테스트가 막는다」로 고쳤다(정책은 그대로).
  ④ BTP `timeout.http.connectionIdleTimeout: 4s` — 상류(uvicorn keep-alive 기본 5초)가 먼저 닫는 연결 재사용 경쟁의 503 UC 방지(egctl 번역:
  cluster `commonHttpProtocolOptions.idleTimeout: 4s`, 라우트 `timeout: 3600s` 그대로). ⑤ 렌더러가 Envoy requests > base limits(1/2Gi)를 FAIL(CRD 는
  받고 EG 의 DaemonSet 생성만 apiserver 에서 거절돼 늦게 드러난다), `PORTAL_TLS_SECRET` 라벨 단위 검사(`a..b`·`a.-b`·`a-.b` 거부). ⑥ 테스트:
  EG 설정 `runtimeFlags` 가 정확히 `enabled: [EnvoyProxyPatch]`(disabled 가 enabled 를 이긴다 — base·두 렌더), HSTS 단언 대소문자 무시,
  EnvoyProxy 의 securityContext·capabilities 0건·`--disable-hot-restart`, BTP 4s, 값 상한·경계.
- **리뷰 2 반영(2026-11-03, 문서·런북만 — 매니페스트 동작 변화 없음)**: ① 원 IP 예외 — PROXY 출발지가 **루프백**이면 Envoy 는 XFF 에 원 IP 대신 자기
  주소(호스트의 첫 비 루프백 주소)를 덧붙인다(Envoy `conn_manager_utility.cc` appendXff). LB 흉내 haproxy 에 127.0.0.1 로 붙는 시험에서만 생기고 prod 의
  SE 는 PC 주소를 싣는다 — EnvoyProxy·CTP 주석, README §2g-5 에 적고 T8 원 IP 시험은 `10.10.10.1:9443` 으로. ② 앱 이벤트의 `peer` 는 마스터의 호스트 쪽
  출발 주소 — Cilium 이면 노드 IP 가 아니라 cilium_host(CiliumInternalIP, dmsk8s cp1 10.245.0.129 — `ip route get` 실측; EG 는 엔드포인트 파드 IP 로
  붙는다). ③ README §2g-5 의 Envoy 로그를 `logs ds/<이름>`(파드 하나만) → 라벨 셀렉터(마스터 전부). ④ external 레지스트리 사이트: lock **전부**
  (gateway·envoy 포함)를 REGISTRY 로 push, §2g-3 의 PriorityClass 를 `get … || apply` 로(§2f-3 과 같게), 증상 표에 레지스트리 주소 ErrImagePull(prod
  README 3b·3c 도). ⑤ 테스트베드 컷오버 순서 — haproxy 를 먼저 bind 하고 DNAT 를 뺀다(되돌림은 DNAT 먼저), host-net 재시작이 ssc 릴레이도 잠깐 끊는다,
  `status` 는 `/etc/default` 를 읽지 않는다(README §2g-7). 테스트 2개: §2g 의 Envoy 로그 명령이 라벨 셀렉터인지, A 경로의 PriorityClass `get || apply`.
- **T8 실증(2026-11-03, dmsk8s — 병행, 전 항목 PASS)**:
  - 설치(A 경로): cp1 sysctl `ip_unprivileged_port_start` 1024 → 443(노드 변경은 그 키 하나), Gateway API v1.6.1 CRD 10개, EG v1.9.3(컨트롤러 w1·
    `dms-addon-critical`·이미지 전부 `10.112.0.50:5000/…@sha256` — docker.io pull 0건), Gateway `Programmed`. Envoy uid 65532 가 cp1 `0.0.0.0:443`
    bind, `ClusterFirstWithHostNet` 으로 xDS(`envoy-gateway.envoy-gateway-system.svc.cluster.local.`), `--disable-hot-restart`(cilium-envoy 재시작 0).
  - luminous haproxy(`send-proxy-v2` — Avi VS-443 흉내) 경유: 포탈 200·로그인, HSTS 없음. 원 IP — 앱 이벤트 `client_ip` = PROXY 출발지, 위조
    X-Real-IP·XFF 무시(앱에 닿은 요청을 tcpdump — `x-real-ip` 0건). 21MiB 413·19MiB 통과(422 — 앱이 본문까지 처리), 3600s 라우트(느린 다운로드
    20.9초 200), PROXY 필수에서 직접 연결 거부·optional 에서 허용, 80 연결 거부.
  - 컷오버 시험: luminous :8443 의 DNAT 4줄(nat PREROUTING·OUTPUT)을 빼 → haproxy → Envoy, LAN·Tailscale 로그인 200, 원 IP 가 실제 PC(옛 경로는
    MASQUERADE 라 10.10.10.1) → 원복(규칙 바이트 동일). 테스트베드 사용자 경로는 ingress-nginx·MetalLB 그대로이고 EG 는 병행(PROXY 필수)으로 남는다.
  - 고친 것(3fd7da9): envoy preStop `httpGet.host: 127.0.0.1` — EG 기본 preStop(host 없음)은 hostNetwork 파드에서 kubelet 이 「failed to find
    networking container」로 실패해 envoy 가 드레인 없이 SIGTERM, shutdown-manager 가 60초 기다려 롤링마다 약 61초 공백(maxSurge 0) → 1.44초.
    Envoy DS `priorityClassName: dms-addon-critical`(없어서 cp1 메모리 압박 때 축출 1순위였다). 접근 로그의 빈 `sni` 필드 제거(hostname 없는
    리스너라 늘 null — 33/33줄).
  - :19001 stats 끔(bd53e9b, 커밋 보안 리뷰): 끄기 전 `0.0.0.0:19001` 이 열려 다른 노드에서 지표 약 1758줄을 읽었다 → 끈 뒤 LISTEN 없음, Gateway
    Programmed 그대로.
  - EG watch = ns dms(79ed341, 백그라운드 보안 리뷰): 모든 네임스페이스를 보면 다른 네임스페이스의 Gateway + EnvoyProxy 패치(EnvoyProxyPatch 가
    켜져 있다)로 PSA privileged 인 `envoy-gateway-system` 에 임의의 특권 파드(hostPath·privileged)를 띄울 수 있었다. 라이브: ns default 의 class `dms`
    Gateway 는 조건 `Unknown` 그대로·프록시 생성 없음, DMS Gateway Accepted·Programmed, VIP 경유 포탈 200. 남이 설치한 EG(C)는 건드리지 않는다(설계 8-2).
  - 용량: EG·GW API CRD 스키마(2.04MB — 클러스터 CRD 스키마의 55%)로 kube-apiserver RSS ≈1GB → 2.5GiB cp1 이 MemoryPressure(speaker 축출 반복) →
    DB primary 를 cp1 밖으로 먼저 옮기고(switchover 7초) cp1 4GiB 로 증설(virsh). testbed IaC(tfvars `memory_mb` 4096·cp1 sysctl drop-in)는 작업
    사본 패치로 인계 대기.
  - 테스트: `test_gateway_contract.py` 추가 2(stats 끔 · preStop host·우선순위·sni 없음), 갱신 1(EG 설정 watch = ns dms).
- **남은 것**: 테스트베드 영구 컷오버(haproxy 를 testbed IaC 의 관리 서비스로 — 그 전까지 사용자 경로는 ingress-nginx), testbed IaC 인계 — BACKLOG
  「⑥ 입구 후속」. 설치기 연결(S8 감지·MetalLB/ingress 게이트 삭제)은 ⑦ 에서 끝났다.

### ✅ ⑤ DB(CNPG) — CloudNativePG 1.30.1 애드온 층·정적 hostPath PV 풀·Cluster dms-pg·pg_dump·경로 프로브·prod 제어면 마스터 배치 — **완료·실증**(2026-11-03, T6·T7·T6')

설계 v6(`docs/history/specs/2026-11-03-dms-k8s-prod-design.md`) 6(6-1~6-6)·#24~#36·#40·2-1/2-2·3-1·8-1(S-1·S2·S9·S10)·8-2(CNPG 행·A 경로·층 배치)·
9절 ⑤·10 T6/T6'/T7·12 위험(#3·#4·#23~#25·#29). 런북은 deploy/README §2f. **DMS 애플리케이션 코드는 바꾸지 않았다**(6-3 — 접속 문자열만, `connect_timeout`
기본 5초 그대로).

- **애드온 층 `deploy/addons/cnpg/1.30.1`**(서드파티 — SSA field manager `dms-install`): 상류 `cnpg-1.30.1.yaml`(1.30.x 최신, 2026-09-23 릴리스 —
  GitHub releases 조회)을 **바이트 그대로** 커밋, `SOURCE`(URL·태그·검증 방법)·`cnpg-1.30.1.yaml.sha256`(`37237f14…`). 검증(S-1): cosign v3.1.4(체크섬
  대조한 공식 바이너리) `verify-blob --bundle cnpg-1.30.1.sigstore.json --certificate-identity …/release-publish.yml@refs/tags/v1.30.1
  --certificate-oidc-issuer https://token.actions.githubusercontent.com` → Verified OK(번들 digest = 파일 sha256, Rekor logIndex 2920200579). DMS 패치
  층(kustomization + `patch-operator.yaml`)이 6-1 airgap 함정을 막는다: 이미지와 env `OPERATOR_IMAGE_NAME`(PG 인스턴스 파드의 bootstrap init 이미지)을
  자리표시자로 두고 렌더러가 **한 변수에서** `<REG>/cloudnative-pg:1.30.1@sha256:<amd64>` 로, `Always` → `IfNotPresent`, env `POSTGRES_IMAGE_NAME` =
  PG 이미지(imageName 을 생략한 Cluster 도 ghcr.io 로 가지 않게 — 함정 3 의 이중 방어), replicas 2, 워커(required nodeAffinity control-plane
  `DoesNotExist`, 두 파드 다른 노드 선호), `priorityClassName: dms-addon-critical`, 라벨 `app.kubernetes.io/managed-by=dms-install`·
  `dms.io/addon-version=1.30.1`(모든 객체 — selector·파드 템플릿에는 넣지 않는다). **`WATCH_NAMESPACE` 는 두지 않는다**(상류 기본 = 모든 네임스페이스,
  dms 포함 — 클러스터에 CNPG 는 하나만 두고 다른 팀이 재사용할 수 있게; 감지기(⑦)는 env·ConfigMap/Secret 의 그 값을 본다). 렌더 결과에 외부 호스트
  0건(설명문까지).
- **DB 층 `deploy/platform/db/{volumes,cluster,backup,probe}`**(DMS 소유 — 렌더 결과 `platform/db-{volumes,cluster,backup,probe}`, S10 순서):
  SC `dms-pg-shared`(no-provisioner·Immediate·Retain·확장 불가) + PV `dms-pgdata-01..05`(hostPath `<SHARED_DIR>/dms/pgdata/dms-pgdata-0N` **`type:
  Directory`**, Retain, RWO, nodeAffinity·claimRef 없음 — 6-2 [확정]) · Cluster `dms-pg`(instances 3, imageName `<REG>/postgresql:17.11-202611020810-
  minimal-trixie@sha256:<amd64>` IfNotPresent, `smartShutdownTimeout: 0`, enablePDB, `primaryUpdateMethod: switchover`(6-5 「롤링 + switchover」 — 상류
  기본은 restart), synchronous `any/1/preferred`, `shared_buffers` 1GB, initdb `dms`/`dms` + Secret `dms-pg-app`, storage size == PV capacity·
  `resizeInUseVolumes: false`, required anti-affinity·control-plane nodeSelector + toleration, 1 CPU/4Gi·메모리 limit 4Gi·CPU limit 없음 — CRD 1.30.1
  스키마로 필드 대조) · pg_dump CronJob `dms-pg-dump`(아래) · 일회용 경로 프로브 DaemonSet. base 는 자리표시자뿐(용량 `set-by-overlay` — raw apply 는
  apiserver·웹훅이 거절).
- **pg_dump CronJob**(6-4): 매시간 `17 * * * *`, `-Fc`, `dms-pg-rw`, 워커, uid 26, hostPath `<SHARED_DIR>/dms/backup/pg`, 100m/256Mi..1/1Gi,
  concurrencyPolicy Forbid. **센티널 `backup/pg/.dms-backup-id`(값 `dms-backup-pg`) 검사만** — 없거나 다르면 덤프하지 않는다(마운트 빠진 워커의 로컬
  디렉터리, 위험 #29; 레지스트리 init 과 같은 이유). 쓰기는 `.partial` → `pg_restore --list` → fsync → 같은 이름이 없을 때만 rename → 디렉터리 fsync —
  단계마다 종료 코드를 보고 실패면 보존 정리 없이 exit 1(리뷰 1회차: fsync·rename 실패를 무시해 새 덤프 없이 「완료」 exit 0 + 옛 덤프 삭제였다).
  보존은 **개수**(`PG_BACKUP_KEEP` 기본 336 = 14일, 형식 `dms-<UTC>.dump` 파일만, 새 덤프 성공 뒤에만 — 시각 기준이면 며칠 실패 동안 좋은 덤프가 다
  지워진다), 3시간 넘은 조각만 정리, 접속 정보는 `dms-pg-app` secretKeyRef 뿐(로그·argv 에 값 없음), 잠금 대기 2분 상한.
- **S0(`deploy/shared-dir-init.sh`)**: 센티널 `dms/backup/pg/.dms-backup-id` 를 더 쓴다(이미 돌린 트리는 다시 돌리면 그것만 만든다 — 테스트베드 T3 트리
  포함).
- **렌더러(`deploy/render.sh`)**: `addons/cnpg`·`platform/db-*` 를 늘 낸다(설계 ⑤ — v4 의 `PG_STORAGE` 키 없음: 로컬 디스크 선택지가 없다). 버전은
  새 커밋 파일 `deploy/versions.env`(`CNPG_VERSION=1.30.1`·`CNPG_OPERATOR_IMAGE`·`PG_IMAGE` = lock DEST — sh 로 . 하지 않고 줄만 읽는다), digest 는
  `images.lock` 의 그 DEST(정확히 한 줄·`sha256:<64 hex>` 아니면 FAIL), 이미지 ref = `<애드온 레지스트리>/<DEST>@<digest>`(애드온 레지스트리 = 클러스터
  내 레지스트리가 있으면 그것 — 앱이 external 이어도, 없으면 REGISTRY). 오퍼레이터 태그 ≠ `CNPG_VERSION`·`addons/cnpg/<v>` 없음·라벨 불일치도 FAIL.
  사이트 값(전부 선택): `PG_STORAGE_SIZE`(20Gi — storage.size **와** PV capacity 를 한 값으로), `PG_CPU_REQUEST`·`PG_MEMORY`(requests == 메모리 limit)·
  `PG_SHARED_BUFFERS`, `PG_PLACEMENT`(control-plane | all-nodes — 테스트베드만, nodeSelector 를 JSON 패치로 뺀다), `PG_BACKUP_KEEP` — 형식 검사, 셸 env
  무시(values.env 만 원천). `render.env` 에 `ADDON_REGISTRY`·`CNPG_OPERATOR_REF`·`PG_IMAGE_REF`·`ADDON_LAYERS`·`DB_LAYERS`(S10 순서)·`DB_PROBE` 등,
  stdout 에 `ADDON:`·`PLATFORM:`(db)·`PROBE:` 줄(마지막 줄은 그대로 `RENDERED:`). ④ 의 `PLATFORM_LAYERS`·레지스트리 동작은 그대로.
- **images.lock**: `cloudnative-pg:1.30.1`(ghcr.io index `sha256:923c267e…` 의 linux/amd64 = `sha256:b0f9805b…`)·`postgresql:17.11-202611020810-minimal-trixie`
  (index `sha256:7c680fc8…` 의 linux/amd64 = `sha256:2eeaa8ed…` — 같은 날 `17.11-minimal-trixie` 와 같은 index; 날짜 붙은 **불변 태그**를 레지스트리
  태그로 쓴다: 움직이는 태그를 덮어쓰면 옛 digest 가 태그 없이 남아 GC 가 지운다). `images-lock.py resolve` 로 만들고 `verify` PASS(ghcr 익명 토큰).
- **도구**: `deploy/pg-path-probe.py`(S10 값 대조·사전 검사·경로 대조, py3.9 — `pv-check`(적용 전, 프로브 없이): 이미 있는 PV 의 hostPath·type·
  capacity·storageClassName 과 SC 의 바꿀 수 없는 필드를 render.env(SHARED_DIR·PG_STORAGE_SIZE)와 대조해 다르면 FAIL — 리뷰 1회차: apiserver 는
  hostPath·volumeMode 만 막고 **용량·SC 이름은 SSA 가 Bound PV 까지 조용히 바꾼다**(k8s 1.34 ValidatePersistentVolumeUpdate); 렌더된 프로브 DaemonSet(모든 노드, uid 26, 읽기 전용, PG_VERSION 은 open 으로
  60초 재시도)을 띄워 노드별 PV 모양·센티널·PG_VERSION 을 모아 판정하고 지운다; precheck = 26:26 0700·`.dms-pv-id`·Available 에 옛 PGDATA 없음, contrast =
  Bound PV 마다 **모든 노드가 같은 PG_VERSION**(곧 인스턴스 아닌 노드도) — 아니면 「PG 가 공용 경로가 아닌 곳에 씀」 FAIL(Cluster `initializingPVC` 의
  PV — initdb·join 중 — 는 UNKNOWN, 리뷰 1회차), FailedMount 는 「마운트 없음」
  FAIL, pull·시간 초과는 모름, 여분 < 1·Released 는 WARN; 시작 때 옛 프로브를 지워 옛 로그를 읽지 않는다, 렌더 디렉터리의 kube-context 와 다른
  `--context` 는 거부) · `deploy/pg-secrets.py`(`ensure-app`·`init-secrets`·`set-url`(옛 URL 은 `dms-secrets-prev`)·`restore-url`·`check` — 6-3 URL
  (`dms-pg-rw`·keepalives 10/5/3·`tcp_user_timeout=15000`·`target_session_attrs=read-write`·`sslmode=require`)을 조립하고 값은 kubectl stdin 으로만,
  출력·argv 0건; `check` 는 어긋난 항목 이름만).
- **prod 제어면 마스터 배치(#24·#25)**: `deploy/overlays/prod/patch-placement.yaml` — dms-api·dms-controller 에 control-plane nodeSelector + toleration +
  not-ready·unreachable `tolerationSeconds: 30`. 오버레이가 제어면 Deployment 를 패치하는 **유일한 파일**이고 내용은 그 두 필드뿐 —
  `test_release_manifest_contract.py` 가 코드 줄을 글자 그대로 고정하고, 렌더 비교에서 prod 만 그 두 블록을 정확히 한 번씩 가진 뒤 나머지는 base 와
  같다(securityContext 등은 여전히 base 가 유일한 진실). testbed-k8s 는 cp1 메모리가 모자라 워커 그대로(설계 10 — 테스트가 「prod 에만」을 고정).
- **guard-images.sh**: 워크로드를 렌더된 `metadata.namespace` 에서 읽는다(전에는 늘 `dms` — CNPG 오퍼레이터(cnpg-system)는 「첫 설치」로 보여 이미지
  교체가 조용히 통과했다). 오퍼레이터 이미지 교체 = CNPG 업그레이드 → exit 3 + 6-5 순서 안내.
- **문서**: deploy/README §2f(구성·값, S2 시크릿(값 출력 없음), S9 감지·SSA·9443 dry-run·증상 표, S10 순서·900초 폴링 세 조건(`wait
  --for=condition=Ready` 금지)·경로 대조·첫 덤프·증상 표, 백업 층·공용 스토리지 밖 사본(암호화)·복구(같은 이름 `dms-pg` 로 다시 만들고 pg_restore)·Released 재활용 런북
  (0단계 마운트 가드·상위 디렉터리 생성 금지), 업그레이드 표(minor 하나씩, **imageName 과 parameters 를 한 번에 바꾸지 않는다** — switchover 모드
  웹훅 거절), 마스터 운영 규칙·장애조치 시간 표, 테스트베드 T6·걷어내기), 머리말, prod README 3b단계·4단계(URL)·선행조건·마스터 배치, prod
  `values.env.example`(PG 키), testbed-k8s `values.env`(100m/256Mi/64MB·all-nodes·보존 24), ARCHITECTURE(모듈 3행·렌더러 행·불변식 4개 + Deployment
  패치 예외), BACKLOG(⑤ 후속 — ⑦ 설치기 S2/S9/S10/S12·`--upgrade-addon`, T6/T6'/T7, 추정 확인 대기, 하지 않은 선택 항목, B4·B1).
- **테스트**: `test_addons_render`(새 — 상류 바이트·SOURCE·sha256, versions·lock·디렉터리·라벨 맞물림, base 자리표시자·외부 호스트 0건, 렌더(testbed·
  prod)의 모든 이미지 = 애드온 레지스트리 + lock digest(태그@digest), `OPERATOR_IMAGE_NAME` == manager·`POSTGRES_IMAGE_NAME` == PG ref, `IfNotPresent`
  뿐, replicas 2·워커·`dms-addon-critical`, 라벨·selector 불변, `WATCH_NAMESPACE` 없음, 웹훅 Fail·cnpg-system, external 이어도 클러스터 내 레지스트리,
  가드가 cnpg-system 에서 읽고 업그레이드를 exit 3 — 상류 `verify` 는 `DMS_NETWORK_TESTS=1` 일 때), `test_platform_db_contract`(새 — base 자리표시자,
  Cluster 고정값·이미지·자원, PV 5개 모양·경로·Retain·nodeAffinity/claimRef 0, size == capacity(한 값), SC, **로컬 디스크 PV·local-path 0건 + 모든 상태
  경로가 같은 `<SHARED_DIR>/dms/` 접두**(③ 조건과 함께), CronJob 모양·비밀 참조·센티널 값 == S0, 덤프 스크립트를 실제 sh + 가짜 pg_dump 로(센티널
  없음·불일치·심링크 → 아무것도 안 만듦, 보존 개수·다른 파일 안 건드림, 실패 때 보존분 유지·조각 없음, 오래된 조각만 정리, 비밀번호 미출력),
  프로브 DaemonSet 모양·스크립트 실행(쓰기 없음), `pg-path-probe.py` 판정·main 순서(옛 프로브 삭제 → apply → 판정 → 삭제)·컨텍스트 고정,
  렌더러 DB 값 검사·기본값·출력 순서·셸 env 무시·versions/lock/addons 불일치 FAIL, `pg-secrets.py`(가짜 kubectl 로 argv·출력에 값 0건, set-url·
  restore-url·check)), `test_release_manifest_contract`(prod 배치 예외를 정확히), `test_shared_dir_init`(백업 센티널), 렌더러 사본 헬퍼 다섯
  곳(`addons`·`versions.env`·`images.lock` 복사). dmsk8s client-side dry-run(`--validate=strict`)으로 db-volumes·db-backup·db-probe·addons/cnpg 통과.
- **리뷰 2회차(2026-11-03)**: 복구 런북을 **같은 이름 `dms-pg`** 로(전에는 새 이름 `dms-pg-r` + set-url — CronJob `PGHOST`·경로 프로브·`pg-secrets.py`
  ·⑦ 설치기가 `dms-pg` 고정이라 매시간 덤프가 버린 옛 DB 를 「완료」로 뜨고 새 라이브 DB 덤프는 0개였다; 앱 0 → Cluster 삭제 → 같은 렌더 SSA →
  pg_restore → 앱 복귀, URL 그대로). Released 재활용 1단계를 PV 기준으로(「그 이름의 PVC 를 쓰는 파드 없음」은 CNPG 1.30 의 번호 재사용 때문에
  정상 재활용에서 늘 거짓 — 이 PV 에 묶인 PVC 없음 + 같은 이름 새 PVC 는 정상 + 강제 삭제였으면 VM 꺼짐 확인). 경로 프로브의 판정 대상을 DMS
  노드(마스터 + 역할 NoSchedule taint 없는 워커 — 설계 2-3, `node.kubernetes.io/*` 상태 taint 는 세지 않음)로 좁히고 `--nodes` 를 더했다(다른
  팀의 taint 노드가 섞인 클러스터에서 그 노드의 FailedMount 로 S10 이 늘 FAIL 했다 — 판정 밖은 목록·INFO). 렌더러: `PG_SHARED_BUFFERS` 가
  없고 `PG_MEMORY` 가 있으면 그 1/4, 실효 메모리의 절반 이상이면 FAIL(PG_MEMORY 만 줄이면 base 1GB 가 남아 limit 을 채웠다). §2f-3: external
  사이트의 PriorityClass 적용·애드온 이미지 push(REGISTRY) 단계와 「파드 0개 + FailedCreate … no PriorityClass」 증상 행. §2f-4 첫 덤프는
  `wait --for=condition=complete`(Failed 에서도 시간 초과까지 기다림) 대신 Complete·Failed 를 함께 보는 루프. §2f 의 렌더 디렉터리 변수는 `RD`
  (§2e 의 `R` = …/platform 과 섞이면 경로가 틀렸다). Cluster 주석의 이관 절 참조(§2f-6 → §2f-5).
- **테스트베드**: T6(push → 오퍼레이터 → 9443 dry-run → SC/PV → 사전 검사 → Cluster → 폴링 → 경로 대조 → CronJob·첫 덤프 → 확인 항목)·T7 은 메인
  세션이 했다(아래 — T7·T6' 는 사용자 승인 뒤).
- **T6 실증(2026-11-03, dmsk8s — 전 항목 PASS)**:
  - airgap: CNPG·PG 파드 이미지 전부 `10.112.0.50:5000/…@sha256`(ghcr.io 0건). 오퍼레이터 w1·w2(`dms-addon-critical`), 웹훅 9443 — Cluster CR
    server dry-run 통과.
  - VAP(09b0652 — 설계 6-2 「권고, 선택」을 기본으로: 볼륨 층에 ValidatingAdmissionPolicy + Deny 바인딩, SC `dms-pg-shared` 의 PVC 는 ns `dms`
    에서만 — claimRef 없는 여분 PV 를 다른 네임스페이스 PVC 가 붙여 DB 디렉터리를 읽는 길, 커밋 보안 리뷰 지적): 다른 네임스페이스의
    `dms-pg-shared` PVC → Forbidden(정책 메시지). 클래스 없이 `volumeName` 으로 고른 PVC 는 VolumeMismatch 로 Pending.
  - 기동: Cluster 적용 → 첫 primary 약 135초(initdb 67초 + template SQL 43초 — NFS fsync), 3/3 Ready 6분 31초. 배치 cp1·w1·w2.
  - 경로 대조 PASS(Bound PV 3개 모두 3노드가 같은 PG_VERSION). 첫 덤프 11초. 커밋 지연 동기 12.8ms / local 7.9ms(복제 비용 약 5ms).
  - 소유 음성(일회용 Cluster): 0:0 디렉터리 → initdb Job 이 `stat …/pgdata: permission denied` 로 반복 실패.
  - PV 순환: 인스턴스 destroy → 새 PVC 가 여분 PV, 옛 PV Released → 재활용 런북 → Available.
  - Cluster 삭제(일회용 — 인스턴스 2·PV 3): PVC 는 Cluster ownerReference 로 약 10초 안에 함께 지워진다(추정 → 실측), PV 2개 Released + 1개
    Available, 같은 이름 재생성은 둘째 인스턴스가 런북 뒤 기동.
  - **옛 PGDATA 가 남은 PV 에 붙어도 실패하지 않는다**(설계 추정 정정): CNPG 1.30 이 같은 PV 안 `pgdata_<UTC>` 로 옮기고 새로 시작한다 — 옛
    데이터가 조용히 남는다 → 경로 프로브가 어느 단계든 `pgdata_*` 를 WARN(1a11d50).
  - 노드 이동(preferred + cordon + 파드 삭제): 다른 노드에서 같은 PV 로 22초, pg_basebackup 없음, system ID·LSN·inode 동일. switchover 는 플러그인·
    수동(`status.targetPrimary` 패치) 모두 약 9초(수동은 phase 가 바뀌지 않음, 옛 primary RESTARTS +1 은 정상).
  - 덤프 → 복원, initdb import(일회용 Cluster) 모두 행 수·md5 일치. switchover 와 겹친 덤프의 첫 시도는 끊기고(`terminating connection`) 재시도가
    성공 → 덤프 Job `backoffLimit` 1 → 3(1a11d50 — 노드 장애조치 1~1.5분에 맞춤).
- **T7 — 테스트베드 앱 DB 이전(2026-11-03, 사용자 승인)**: pkg-01 `dmsdb_k8s`(PG 17.10) → `dms-pg`(17.11), 덤프·복원(README §2f-2 「이관 순서」).
  - 동결(DMS drain on·CronJob suspend·api/controller 0 — 파드가 사라질 때까지) — 앱 정지 창 3분 29초. 덤프 1초(3.4MB)·`pg_restore
    --single-transaction` 16초, 테이블 37·행 42,951·시퀀스 7 일치(행별 md5), `set-url` → `check` PASS, migrate no-op, API 스냅숏 동일,
    `pg_stat_activity` 에 api·controller(TLS 1.3).
  - 대조 1회차: 행 텍스트를 통째로 `string_agg` 하는 SQL 이 대상 primary 백엔드를 183MB 로(agent_reports 54MB) — PG_MEMORY 256Mi cgroup OOM →
    장애조치. 행 해시만 모아 다시 돌려 PASS(README §2f-5 「대조」).
  - switchover 5xx 창 약 7초(controller 는 DB 를 잃으면 재시작 — 정상), 수동 덤프 6초. pkg-01 `dmsdb_k8s` 는 손대지 않음(되돌림 = `restore-url`).
  - 고친 것: `pg-secrets.py set-url` 이 되돌림 URL 을 덮던 결함(a22cb32 — 이관 뒤 다시 돌리면 옛 값이 이미 CNPG URL 이라 `dms-secrets-prev` 의
    외부 DB URL 이 사라졌다. 옛 값이 이 클러스터 `-rw` 면 prev 를 그대로 두고, prev 에 다른 외부 URL 이 있으면 `--overwrite-prev` 없이는 멈춘다).
  - 테스트베드 용량: ⑤ 뒤 워커마다 +200m(PG 인스턴스 100m + 오퍼레이터 100m) — dms-controller 위치에 따라 2노드 dscan gang(워커 1000m +
    launcher 100m)이 5m 모자라 대기 → testbed `PG_CPU_REQUEST` 100m → 50m(values.env, 계약 테스트 값 갱신).
- **T6' — 장애·성능(2026-11-03, dmsk8s, 앱이 `dms-pg` 위에서 — 4 를 빼고 PASS, 4 는 설명된 편차)**:
  1. **primary 노드 전원 차단**(`virsh destroy k8s-w1`): 노드 unreachable +4초 → 장애조치 시작 +7초 → 새 primary +25초 → rw 전환 +30초 — 전원 차단부터
     **77~79초**. keepalive 클라이언트 공백 79초(`tcp_user_timeout` 15초로 16초에 감지), **keepalive 없는 클라이언트는 ETIMEDOUT 까지 15분 52초**(약 15분
     가설 확인 — 6-3 URL 의 keepalive·`tcp_user_timeout` 이 필요한 이유). 앱(VIP `/readyz`) 78초 불가(처음 약 30초는 매달림, 뒤는 ingress 503), rw 전환
     2초 뒤 복귀. dms-controller 는 DB 를 잃으면 exit 1 → 재시작(DB 복귀 약 16초 뒤). 복구: `virsh start` 35초 뒤 노드 Ready, 옛 primary 는 pg_rewind
     (630MB)로 재합류 → 약 2분에 3/3. 꺼진 노드의 파드는 Terminating 그대로 뒀다(강제 삭제 없음 — 6-6).
  2. **drain `smartShutdownTimeout` 0 대 180**: 같다 — drain 12초, 앱 503 5.0초 대 4.0초, 쓰기 공백 약 8초 대 약 9초, uncordon 뒤 3/3 약 12초 대 약 11초.
     drain 은 CNPG switchover 를 거치므로 smart 대기가 쓰이지 않는다(180 은 switchover 없는 종료에서만 — 시험 안 함). 렌더 값 0 으로 원복. promote
     5회 모두 앱 503 5~6초.
  3. **apiserver 불통 약 71초**: PG 쓰기 공백 0, 앱 5xx 0, PG 파드 재시작 없음. 오퍼레이터 leader·kcm·scheduler·cilium-operator·csi-nfs-controller·
     envoy-gateway 재시작(lease 상실 — 정상).
  4. **primary 노드의 NFS 정지**(호스트 iptables DROP 93초, 자동 해제 타이머를 먼저 걸고): durable 쓰기(`pg_logical_emit_message(true, …)`)가 즉시 멈춤.
     CNPG 는 27초에 primary NotReady, 57초에 「initiating a failover」였지만 **정지 동안 장애조치는 끝나지 않았다**(옛 primary 가 멈추기를 기다린다).
     해제 뒤 옛 primary 에 쌓인 커밋이 flush → 새 primary +8초, 3/3 +23초. 앱은 정지 31초째부터 해제 14초 뒤까지 76초 불가, D 상태 프로세스 최대
     10개(5초 안에 해소), 데이터 손실 없음(두 회차 모두 「no rewind required」). 운영 의미: primary 노드의 스토리지만 멈추면 자동 장애조치가 막힌다 —
     길어지면 사람이 조치한다(예: 그 노드 전원 차단 → 1 의 경로, 시험 안 함). 1회차 측정은 무효였다(`txid_current()`·`select now()` 표본은 durable WAL
     쓰기를 하지 않는다) — 런북은 `pg_logical_emit_message(true, …)` 로 잰다.
  5. **replica 노드 재마운트 절차**(3-2 G2 보충): umount·mount 성공, 3/3 42초, 앱 오류 0.
  6. **fsync 공용 NFS 대 로컬 emptyDir**(w1): fdatasync 328 대 319 ops/s(≈3ms), fsync 310 대 159 — 둘 다 같은 호스트 디스크 위라 prod 스토리지를
     대표하지 않는다.
  - 뒤: CNPG 오퍼레이터 두 레플리카가 모두 w2 에 남았다(preferred anti-affinity 는 다시 펼치지 않는다) → 메인 세션이 w1·w2 로 다시 펼쳤다(BACKLOG).

### ✅ ④ 클러스터 내 레지스트리 — Distribution v3.1.2 플랫폼 층·렌더러 일반화·images.lock·가드 — **완료·실증**(2026-11-03, d19 — 앱 전환)

설계 v6(`docs/history/specs/2026-11-03-dms-k8s-prod-design.md`) 7(7-1~7-6)·2-1(PriorityClass)·8-1(S0-4·S0-5·S-1·S4~S6)·8-2 「층 배치」·
9절 ④·10 T4, 사용자 결정 #46(1개·오퍼레이터 없음). 런북은 deploy/README §2e. DMS 코드(`registry.py`·`routes_registry.py`)는 바꾸지 않았다.

- **플랫폼 층(DMS 소유, `overlays/`·`k8s/` 밖 — SSA field manager `dms-install`)**: `deploy/platform/priorityclass` = PriorityClass
  `dms-addon-critical`(1000 > dms-high 200, globalDefault false — ⑤⑥ 애드온 컨트롤러가 잡을 선점할 수 있게). `deploy/platform/registry` =
  ConfigMap `dms-registry-config`(`config.yml`: rootdirectory `/var/lib/registry`·delete 켬·uploadpurging 168h/24h·readonly 끔·**cache 없음**·
  `:5000`·health.storagedriver — 이미지 기본의 debug :5001 을 덮음, + `expected-registry-id`) · Deployment `dms-registry`(replicas 1,
  control-plane nodeSelector + toleration, not-ready·unreachable 30초, RollingUpdate maxSurge 1/maxUnavailable 0, hostPath
  `<SHARED_DIR>/dms/registry` `Directory`, 이미지 `<IP>:<PORT>/registry:3.1.2` **태그 ref 예외**·IfNotPresent, `REGISTRY_HTTP_SECRET` 은 Secret
  `dms-registry-http` 참조, readiness `GET /v2/_catalog?n=1`(5s/10s/3), liveness TCP, **검사만 하는 init**(센티널 없음·불일치·읽을 수 없음·
  기대값 없음을 다른 문구로, 저장소는 읽기 전용), 2-1 자원) · Service `dms-registry`(clusterIP 고정). base 는 사이트 중립 자리표시자뿐이라
  raw apply 는 실패한다.
- **보안 강화(설계 7-3 「(선택)」 — 했다)**: 파드 uid/gid 65532(S0 의 저장소 소유 65532:65532 0750 과 같음 — 테스트가 두 파일을 대조),
  drop ALL·읽기 전용 rootfs(/tmp emptyDir)·SA 토큰 없음. root 면 저장소 파일이 0 소유로 섞이고, drop ALL 인 root 는 그 디렉터리에 쓰지도 못한다.
- **렌더러 일반화**(설계 9 ③·④): `deploy/render.sh <site>` 하나가 앱 오버레이(prod 는 템플릿 치환, testbed-k8s 는 커밋된 오버레이 복사 —
  둘 다 `../../k8s` 깊이 보정)와 플랫폼 층을 `deploy/.rendered/<site>/{app,platform/priorityclass,platform/registry,render.env}` 에 낸다(.gitignore).
  옛 `deploy/overlays/prod/render.sh` 는 호환 진입점(`render.sh prod`), 결과 위치가 `.prod-rendered` 에서 바뀌었다. 실패는 임시 디렉터리에서
  끝나 이전 결과를 바꾸지 않는다. 새 values 키: `REGISTRY_MODE`(in-cluster|external — 없으면 external), `REGISTRY_CLUSTER_IP`(IPv4 검사 —
  있으면 레지스트리 층을 렌더, external 이어도 병행), `REGISTRY_PORT`(5000), `REGISTRY_TLS_VERIFY`(in-cluster 면 false 만), `SHARED_DIR_ID`
  (센티널 기대값 = S0 의 `.dms-shared-id` — **값의 원천을 values 로 둔 이유**: 설치 호스트는 공용 스토리지를 마운트하지 않을 수 있고, 렌더가
  호스트 상태에 기대지 않게), 선택 `REGISTRY_CPU_REQUEST`·`REGISTRY_MEMORY_REQUEST`. in-cluster 면 `REGISTRY` 를 비우면 `<IP>:<PORT>` 로
  채우고 다르면 FAIL, 렌더 결과에서 **같은 주소 다섯 곳**(REGISTRY·newName·`DMS_JOB_IMAGE`·`DMS_BUILD_REGISTRY`·`DMS_BUILD_BUILDER_IMAGE`)을
  다시 대조한다(external 이면 빌더 미러만 예외). testbed-k8s 는 앱이 `pkg-01:5001` 그대로(external — T4 병행, 실증 뒤 in-cluster 로 전환 — 아래 「T4 실증」)이고 values.env 에
  `REGISTRY_CLUSTER_IP=10.112.0.50`·`REGISTRY_PORT=5000`·`SHARED_DIR_ID`(실측)·requests 50m/64Mi 를 기록했다 — 렌더된 앱 == 커밋된 오버레이.
  셸 환경의 레지스트리 키는 읽지 않는다(values.env 만 원천 — ③ 의 SHARED_DIR 와 같은 이유).
- **images.lock**(설계 7-5 ①, 커밋): `DEST SOURCE DIGEST` 세 칸(sh `read -r` 로도 읽힘) — `registry:3.1.2`(docker.io, amd64 `sha256:1290056e…`)·
  `buildah:stable`(quay.io v1.43.1, amd64 `sha256:7bb110e1…` = 테스트베드 `pkg-01:5000/buildah:stable` 미러와 같은 digest). digest 는
  레지스트리 HTTP API(익명 토큰)로 받아 index 면 linux/amd64 항목을 골랐다(본문 sha256 재계산). `deploy/images-lock.py`(python 3.9):
  `check`(형식 — 칸 수·호스트 없는 DEST·태그·digest 모양·DEST 중복), `verify`(상류에서 index 아님·linux/amd64 — 인터넷 노드), `resolve`
  (한 줄 생성). 상류 무응답은 모름(exit 4).
- **guard-images.sh**: 렌더된 레지스트리 층에도 같은 가드 — 저장소 hostPath 변경 exit 5(새 경로의 S0·소유 보존 복사 안내), 이미지 변경 exit 3
  (7-6 업그레이드 순서: push → 마스터 전부 crictl pull·고정 확인 → 적용), DMS 소유 층이면 다음 명령을 SSA 로. 기존 exit 3/4/5 의미는 그대로.
- **install.sh**: 렌더 호출을 `render.sh prod` 로. in-cluster 면 호스트 push 를 건너뛰고(S5 는 인터넷 노드), Service `dms-registry` 가 없거나
  clusterIP 가 values 와 다르거나 Ready 가 아니면 apply 전에 멈춘다. S4~S6 자동화는 ⑦(BACKLOG).
- **문서**: prod `patch-config.yaml` 의 「TLS 레지스트리 권장」 주석 정정(평문 HTTP·익명만 된다 — 7-2), values.env.example·prod README(레지스트리
  키·선행조건), deploy/README §2e(S0-4 신뢰 drop-in `50-dms-registry.conf`, S0-5 oci-archive 반입·`99-dms-pinned.conf` 고정 병합, S4 SSA·
  http 시크릿 stdin 생성·catalog 확인·증상 표, S5 lock push·digest 대조, S6 crictl pull, 업그레이드·GC·백업·장애, 테스트베드 T4·전환 절차),
  ARCHITECTURE §6(모듈 3행·불변식 3개).
- **테스트**: `test_platform_registry_contract`(새 파일 — 설계 9 ④ 줄 전부 + base 자리표시자·config·프로브·securityContext↔S0·init 을 실제
  sh 로 돌려 「만들지 않음」·렌더러 prod/testbed·실패 케이스·testbed 전환 모양), `test_images_lock`(새 파일 — 형식·sh 파싱·상류 바꿔치기로 index/
  arm64/digest 불일치/모름/토큰/resolve·3.9 문법), `test_guard_images_context`(레지스트리 층 3/5/4·SSA 안내), 렌더러 이동에 맞춘
  `test_prod_k8s_overlay`·`test_shared_dir_contract`·`test_check_scripts_context`(복사 대상·결과 위치·견본 IP). 관련 16개 파일 379 passed·1 skipped
  (testbed-k8s 앱 전환 모양을 사본에 적용해 같은 계약이 `test_testbed_k8s_overlay` 상수만 고치면 통과함도 확인 — 「전환 전」 전용 2건은 skip).
- **리뷰 1회차(2026-11-03)**: init·본 컨테이너 둘 다 `IfNotPresent`(Always 0건 — 자기 이미지를 자기에게서 pull 하는 교착)·PriorityClass
  `preemptionPolicy: PreemptLowerPriority` 를 테스트로 고정, Deployment 에 라벨 `dms.io/addon-version: "3.1.2"`(설계 8-2 A — 이미지 태그·lock 과
  같이 움직이는지 테스트), 렌더러가 셸 env 의 태그·도메인·VIP·옛 LDAP 키도 읽지 않게(unset), `images-lock.py check` 가 sh 소비자와 같은 줄·칸
  규칙(CR·비 ASCII·들여쓴 주석 거부), 가드 exit 4 의 「맞는 짝」 안내도 플랫폼 층이면 SSA. 문서: §2e-3 맨 앞에 네임스페이스(S2 —
  새 클러스터에서 S4 가 `namespaces "dms" not found`), 증상 표 첫 줄 = FailedMount(hostPath type 검사 — init 전, 가장 흔함, init 「센티널 없음」은
  경로는 있을 때만), port-forward listen 대기, GC 의 `kubectl edit` 에 `--context`·`-n`, 걷어내기 절차, skopeo 가 실제로 까는 것
  (shortnames.conf·registries.conf·policy.json·CNI 플러그인 — storage.conf 아님)과 `--no-install-recommends`, lock 머리에 Volcano 3종(⑦ S9b).
- **리뷰 2회차(2026-11-03)**: install.sh 의 in-cluster 게이트가 Ready 뒤 port-forward(무작위 로컬 포트, listen 줄을 기다림)로 dms·dms-agent·
  dms-mpifileutils 매니페스트를 HEAD 해 없으면 apply 전에 「S5 미완」으로 멈춘다(전에는 migrate ImagePullBackOff → 180초 타임아웃) — 태그는
  render.env 에 새로 적는 해석값(`live` 포함). 렌더러: 레지스트리 파드 템플릿에 `dms.io/config-sha256`(= sha256(config.yml + expected-registry-id)
  — 기동 때만 읽혀 설정만 바뀐 SSA 재적용이 반영되지 않던 것), in-cluster 에서 REGISTRY 를 비웠으면 미치환 목록에서 빼고(비워 두라던 값을
  채우게 하던 것) REGISTRY_CLUSTER_IP·SHARED_DIR_ID 에는 값을 얻는 곳을 한 줄씩. 가드 exit 5(레지스트리 저장소 이동) 안내를 「새 S0 →
  `rsync --exclude=/.dms-registry-id`(통째 복사는 새 센티널을 옛 값으로 덮어 「센티널 불일치」) → `SHARED_DIR_ID` 갱신·재렌더 → 허용 SSA」 로.
  문서: §2e-5 「설정 변경」·「저장소 이동」, §2d-4 의 in-cluster 하위 단계(앱보다 먼저), GC 길 B 의 원복은 SSA `--force-conflicts` + `rollout
  restart`, 「센티널 불일치」 행에 두 파일 대조·고치는 순서, REGISTRY_CLUSTER_IP 의 「S1 이 본다」를 지금의 손 확인 명령(servicecidr·
  ipaddresses·하단 정적 대역)으로 — S1 자동 검사는 ⑦, 렌더 명령에 `--context`, deploy/README 머리말에 「in-cluster 면 §2e 도」, prod README
  의 S7 프로브를 S6 뒤로(render.env 의 REGISTRY), skopeo 의 `/usr/lib/cni` 는 추천이 아니라 containers-common 의 필수 의존(CRI-O `plugin_dirs`
  밖이라 무해 — cp1·w1 `apt-get install -s` 확인).
- **테스트베드**: T4(cp1 반입·고정 → 10.112.0.50 레지스트리 → k8s-w1 에서 lock·d17 push → 3노드 pull → 확인 항목)와 앱 전환은 메인 세션이
  했다(아래). 라이브는 d19 = `10.112.0.50:5000`, `pkg-01:5001` 은 되돌림용으로 둔다. testbed IaC(crio 롤 drop-in 둘·skopeo)는 testbed 저장소
  몫(작업 사본 패치).
- **T4 실증(2026-11-03 — 전 항목 PASS)**:
  - 노드: 3노드 `50-dms-registry.conf`(reload 로 `10.112.0.50:5000 Insecure:true`), w1·cp1 skopeo 1.13.3(설정 diff 는 policy.json·registries.conf·
    shortnames.conf 추가뿐, `/usr/lib/cni` 는 생기지만 CRI-O plugin_dirs 는 `/opt/cni/bin` 뿐), cp1 oci-archive 반입 → repoDigest = lock
    `1290056e…` → `99-dms-pinned.conf` reload 만으로 pinned true(재부팅 뒤 유지).
  - S4: SSA 3초 롤아웃, init 「센티널 확인」, 빈 catalog 200(3노드 호스트 → ClusterIP). S5: lock 대조 OK(registry·buildah). S6: 5 ref × 3노드 pull.
  - 센티널 음성: 없는 디렉터리 → Init:Error·만들지 않음, 기대값 불일치 → 거부, 대조군 Completed.
  - 업그레이드(maxSurge 1): 매니페스트 GET 172회 실패 0. GC: env readonly JSON 파싱 OK(POST 405) → 포탈 삭제 409 `registry_delete_disabled` → GC → 끄기 202.
  - cp1 재부팅: 실패 창 약 93초(레지스트리는 고정된 자기 이미지로 같은 파드 재시작, 부팅 뒤 pull 0회).
  - 앱 전환(16): 오버레이 newName·`DMS_JOB_IMAGE`·`DMS_BUILD_REGISTRY`·빌더 → 10.112.0.50:5000(6287cd8), 가드 exit 3 → 허용, migrate 4초, 잡 이미지
    재릴리스(DB 오버라이드 → `10.112.0.50:5000/dms-mpifileutils:d18`), 모든 dms 파드 새 주소, 작은 scan 성공. 첫 포탈 빌드 d19(소스 01fd922)가 새
    레지스트리에 push → 없는 태그 릴리스 422 `unknown_tag` → 4종 릴리스 Applied(동봉 매니페스트가 새 주소로 스탬프돼 드리프트 「일치」) → 노드
    pull → 이미지 관리 화면 d19 3종 사용 중 보호(35a10a3).
  - 실증에서 찾아 고친 것: 레지스트리 종료 drain 없음(SIGTERM 에 exit 2 — 실제로 진행 중 전체 이미지 pull 이 5분 매달린 뒤 실패) →
    `http.draintimeout 20s` + preStop 5s(b3c5f4e) — 이후 「Draining connections for 20s」·exit 0, 교체 중 이미지 복사 62회 실패 0.
    포탈 이미지 삭제 보호 두 구멍(01fd922): DB 잡 이미지 실효값 미보호, digest 를 공유하는 사용 중 태그가 함께 지워짐 → 409 `registry_digest_in_use`
    (reasonCodes.json·REASON_MESSAGES 양쪽; `registry_delete_disabled` 문구는 GC 읽기 전용 창도 말한다).
  - 실측: 레지스트리 RSS 43~50MB(대량 pull 때 최고 약 66MiB — 테스트베드 request 64Mi 를 살짝 넘음, limit 512Mi).
  - 전환 뒤 계약: `test_platform_registry_contract`·`test_testbed_k8s_overlay` 102 passed·2 skipped(사본에 전환을 적용하던 2건 — 이미 전환됐다).

### ✅ ③ 공용 디렉터리(SHARED_DIR) — 아티팩트 경로 한 원천(`<SHARED_DIR>/dms/artifacts`)·hostPath 가드(exit 5)·스토리지 등록 가드·S0 초기화·S7 동작 프로브 — **완료·실증**(2026-11-03, d18)

설계 v6(`docs/history/specs/2026-11-03-dms-k8s-prod-design.md`) 3-1·3-2·8-1 「S0·S7 세부」·9절 「③ 공유 FS 세부」·10절 T3, 사용자 결정
#45(테스트베드도 DMS 상태를 모든 노드가 마운트한 공용 경로에)·#47(G3 는 필수 아님 — WARN). 런북은 deploy/README §2d.

- **배치·원천 하나**: base(20-config·40-api·41-controller)의 아티팩트 경로(api/controller hostPath·mountPath·`DMS_ARTIFACT_BASE_URI`·
  `DMS_ARTIFACT_BASE_ALLOWED_PREFIXES`)를 자리표시자 `/set-by-overlay.invalid/dms/artifacts` 로 바꿨다(볼륨 이름 `cephfs` → `dms-artifacts`,
  raw apply 는 없는 경로라 뜨지 않음). 오버레이(prod·testbed-k8s — 같은 블록)의 kustomize `replacements` 가 `DMS_ARTIFACT_BASE_URI`
  (`file://<SHARED_DIR>/dms/artifacts`) 하나에서 allowlist 와 hostPath·mountPath 를 낸다 — hostPath == mountPath == allowlist == env base,
  `type: Directory` 만. allowlist 는 공유 FS 최상위(`/cephfs`)에서 `<SHARED_DIR>/dms/artifacts` 로 좁아졌다. 「오버레이는 제어면 Deployment 를
  패치하지 않는다」 계약은 「이미지·이 경로 두 필드 말고는 base 와 같다」로 좁혔다. 「DirectoryOrCreate 로 바꿔라」 주석을 지웠다.
- **prod values**: `SHARED_FS`(상대 이름) → `SHARED_DIR`(절대 경로, `/` 아님, 정규화, `^/[A-Za-z0-9._/-]+$`). 옛 키가 남으면 render FAIL +
  이행 안내 세 가지(DB base·겹치는 스토리지·guard exit 5). 셸 환경의 `SHARED_FS`·`SHARED_DIR` 는 무시한다(values.env 만 원천). prod
  README·values.env.example·patch-config(allowlist 키 삭제 — 두 원천 금지)·preflight 수동 확인 줄·install.sh 가드 안내를 함께 고쳤다.
- **testbed-k8s**: `values.env`(`SHARED_DIR=/dms_shared`)를 커밋했다. 렌더 원천은 `patch-config.yaml` 의 base
  `file:///dms_shared/dms/artifacts` 이고 values.env 는 같은 값의 기록이다(일치는 테스트가 강제, 소비자는 S7 프로브의 `--overlay`).
- **guard-images.sh**: 렌더된 Deployment 의 hostPath 집합 != 라이브면 exit 5, `ALLOW_HOSTPATH_CHANGE=1`(`--allow-hostpath-change`)로만
  통과 — 이미지(exit 3)가 먼저. 새 경로가 없는 노드의 FailedMount·옛 마운트 + 새 base 로 재시작한 파드의 summary 못 읽음을 막는다.
- **스토리지 등록 가드**: mount_path·managed_root 가 DMS 상태 디렉터리(`artifact_base.dms_state_roots` — env·유효 base 가 `…/dms/artifacts`
  꼴이면 그 부모, 아니면 base 자신)와 같음·아래·덮음이면 422 `storage_overlaps_dms_state`(생성·경로 변경만 — 비활성화·범위 토글은 막지
  않음; 선두 `//` 는 `invalid_storage`). 「아티팩트 경로」 화면(`GET /api/admin/artifact-base` 의 `dms_state`)이 상태 디렉터리와, 그것과
  겹치는 기존 스토리지를 경고로 보인다. 사유 코드는 reasonCodes.json·REASON_MESSAGES 양쪽.
- **S0** `deploy/shared-dir-init.sh`(새 파일): 마운트 가드(`[ -d ]` + 포함 마운트 TARGET ≠ `/`) 뒤 3-1 트리와 센티널(`.dms-shared-id`·
  `registry/.dms-registry-id`·`.dms-pv-id` ×5). SHARED_DIR·상위는 만들지 않고, 이미 있으면 검사만 한다(`--check` 는 검사만; 종료 0/1/2/4).
  설계가 테스트베드 플레이북(`dms-shared-fs.yml` 의 FSTYPE == ceph 단정)의 일반화로 둔 가드를 이 스크립트가 맡고, 플레이북은 그대로 뒀다.
- **S7** `deploy/shared-storage-probe.py`(새 파일): 노드별 일회용 Pod(설계의 「임시 DS」)로 G1·G3·G4·G5·G9·fsync 를 동작으로 판정한다
  (FS 종류·옵션으로는 FAIL 하지 않음, G3 는 WARN, 교차 노드는 재시도 뒤 판정; 종료 0 PASS·3 WARN·1 FAIL·4 모름·2 사용법).
- **최종 리뷰에서 고친 것**: 프로브 정리가 남 소유 0700 디렉터리(CephFS 등 — 파드 cap 에 DAC_OVERRIDE 없음)에서 `.probe-<nonce>` 를
  남김 → lchown·fchmod 로 되찾은 뒤 지움(링크는 안 따라감) · 센티널이 모든 노드에 없을 때 WARN → FAIL 「센티널 없음(S0 미실행)」(그래서
  exit 3 = G3 만) · 셸의 `SHARED_DIR` 가 values.env 의 빠진 줄을 채움 → 무시 · S0 스크립트가 없음 → 신설.
- **테스트**: `test_shared_dir_contract`·`test_shared_dir_init`·`test_shared_storage_probe`·`test_storage_dms_state_guard`(새 파일),
  `test_guard_images_context`(hostPath)·`test_release_manifest_contract`(경로 예외)·`test_prod_k8s_overlay`·`test_check_scripts_context`,
  포탈 `ArtifactBasePage.test.tsx`(겹침 경고). 관련 11개 파일 240 passed·1 skipped(root 가 아니라 건너뜀).
- **테스트베드**: 공용 디렉터리(호스트 `10.10.10.1:/data/dms_shared` NFS export → 3노드 `/dms_shared`, `lookupcache=positive`)는 라이브에
  적용했다(IaC 반영은 BACKLOG). 옛 base `/cephfs/dms/artifacts` 는 되돌림용으로 보존한다.
- **T3 실증(d18, 2026-11-03 — 전 항목 PASS)**:
  - S0: w1 에서 트리·센티널 생성(RESULT PASS), cp1·w2 `--check` PASS(같은 센티널). 가드 음성: cp1 의 `/cephfs`(마운트 없음)는 rc=1, 아무것도
    만들지 않음.
  - S7: `/dms_shared` 3노드(cp1 포함) ①~⑥ PASS(exit 0). 음성 대조 — `SHARED_DIR=/cephfs` FAIL(cp1 「마운트 없음」·w1/w2 「센티널 없음」,
    CephFS 위 `.probe-*` 잔여 0), root_squash 임시 export → ③ G4 「root 소유 변경(squash)」 FAIL(exit 1). `nosharecache,lookupcache=all`
    (+`nocto`·60초 속성 캐시) 임시 마운트는 **PASS** — 설계는 ② WARN 을 기대했지만(추정) 커널 6.8 은 디렉터리가 바뀌면 부정 dentry 를 다시
    확인해 노드 셸 직접 측정에서도 새 이름이 즉시 보였다(② 는 동작 판정이라 PASS 가 맞다). 이 대조에서 찾은 프로브 결함(새 이름·rename 교체를
    한 루프에서 봐 교체 쪽 LOOKUP 이 부정 dentry 를 무효로 만듦)은 고쳤다(4b81774 — 차례로 확인, prime 의 부정 조회는 마지막).
  - 전환: 옛 base 를 w1 에서 `rsync -aH --numeric-ids` 로 복사 → 대조(740 항목 소유·모드·크기 일치 — 디렉터리 크기는 뺐다: CephFS 디렉터리
    크기는 rbytes) → guard 가 플래그 없이 exit 5(라이브 `/cephfs` → `/dms_shared/dms/artifacts`) → drain → 차이분 rsync → `ALLOW_HOSTPATH_CHANGE=1`
    적용(migrate·재시작·verify PASS 4/0) → drain 해제. base 출처가 env 라 DB 재저장은 필요 없었다. 3홉(API·컨트롤러·w1·w2) 모두 정상.
  - 기능: 전환 전 잡(scan·nsync·dsync)의 결과 파일·로그가 복사본에서 열림, 새 scan(w1·w2)·다중 노드 dsync 미리보기 → 확인 → 성공(결과 파일이
    cp1·w1·w2 세 노드에서 같게 보임), 사용량 분석 엔드포인트 정상, rm 으로 목적지 정리, 요청 삭제 → 정리 대기 0·결과 디렉터리 삭제.
    등록 가드 — `/dms_shared`·`/dms_shared/dms`·`/dms_shared/dms/x`·끝 슬래시·관리 루트 겹침·기존 스토리지 경로 변경 모두 422
    `storage_overlaps_dms_state`, 형제 `/dms_sharedx`·`/dms_shared/dmsx` 는 통과. 「아티팩트 경로」 1280·375·보안 이벤트 375(쌓기) — 콘솔 오류 0,
    가로 넘침 0.

### ✅ ② 로그인·식별 — 아이디 기준 상한·원 IP 는 기록 전용·인증번호 6자리·대기형 보호 장치·관리자 구제·보안 이벤트 화면 — **완료·실증**(2026-11-03, d17)

설계 v6(`docs/history/specs/2026-11-03-dms-k8s-prod-design.md`) 5절·9절 ②, 사용자 결정 #15~#23(비밀번호 최소 규칙은 넣지 않음,
인증번호 전체 오답 1000회/시간, 전체 스위치는 기본 꺼짐·대기형, 선택 4종 모두 포함).

- **식별**: IP 축 셋(로그인 `ip:` 키·인증 메일 IP 별·인증번호 IP 별)을 없앴다. 원 IP(`auth.client_ip` — XFF 마지막 항목(IP 검증) →
  소켓 피어, **X-Real-IP 무시**)는 보안 이벤트 payload `{client_ip, peer}` 와 경보에만 쓴다. SNAT·NAT 뒤 전원이 함께 잠기던 문제
  (테스트베드 ingress 출발지가 모두 10.10.10.1)가 사라진다.
- **인증번호**: 6자리(2026-08-20 의 4자리를 대체)·5분·코드당 5회·아이디별 10회/24h + **전체 오답 상한 1000회/3600초**(거절형 429
  `verification_global_locked`, 발급도 거절 — 옛 `verification_client_locked` 삭제).
- **로그인**(`routes_auth.login` async, `api/login_guard.py`): 아이디 실패 10/60s + 아이디별 총 시도 60/60s(성공 포함) → 봉인
  해제 → 대기형 전체 스위치(기본 꺼짐, 켜면 토큰 버킷으로 늦추고 예상 대기 > M 이면 503 `login_busy`) → scrypt 대기형 슬롯 2개(이벤트
  루프에서 최대 5초, 넘치면 503) → 슬롯 안 재확인 → 검증 → 401 이 아니면 토큰 환불. `/healthz` async(대기 중에도 응답). 경보 3종
  (실패·전체 시도·출발지별 실패 — 공격자가 고르는 키의 이벤트는 종류별 창당 50건).
- **관리자 구제·세션**: 관리자 비밀번호 재설정 `POST /api/admin/accounts/{u}/password-reset`(세션 관리자·봉인 용도
  `admin_password_reset`·특권 가드·자기 자신 409 `cannot_reset_self`), 인증번호 잠금 해제(아이디별·전체 — `routes_security`),
  비밀번호가 바뀌면 `accounts.session_epoch`(난수) 회전 → 옛 세션 401 `session_revoked`(CREATE TABLE + `_ensure_columns`),
  가입·비밀번호 변경 완료 안내 메일(최선 노력, `api/account_notice.py`).
- **포탈**: 관리 → 「보안 이벤트」(보호 장치 상태·인증번호 잠금 표와 해제·이벤트 목록 — 종류·심각도·검색·키셋 무한 스크롤, 해제 결과는
  늘 있는 결과 줄로 알리고 포커스를 옮김), 계정 화면 「비밀번호 재설정」(봉인, 자기 행 없음, 안내 메일 결과 문구), 로그인 화면 6자리
  인증번호 칸·429/503 남은 시간 안내·2초 넘으면 대기 안내·비밀번호 변경으로 끝난 세션 안내, 375px 결함 2건 수리.
- **리뷰**: 계약 → 백엔드·포탈 병렬 구현 → 리뷰 1라운드(백엔드·포탈 UX·보안/회귀) → 수정 → 2라운드(새 눈, 지적 7건 — 실 Chrome 에서
  찾은 「전체 해제 결과 줄이 안 뜸」 포함) → 수정.
- **테스트**: 백엔드 새 파일 6개(login_guard·login_concurrency·admin_password_reset·session_revocation·api_security 외) + 기존 파일 갱신,
  포탈 SecurityPage·ResetPasswordDialog·Login 등, e2e E17(관리자 재설정 → 다른 브라우저 401 session_revoked 안내·옛/새 비밀번호·보안
  이벤트 행·1280/375) + E3 순회에 보안 이벤트.
- **검증(①·② 함께)**: 전체 백엔드 4588 passed(스냅숏), vitest 1847, tsc(앱·e2e), e2e 32(E17 첫 실행 1건 실패 = 테스트 결함 — 종류 코드
  칸을 부분 일치로 찾아 같은 조치의 완료 안내 메일 행을 잡음 → 정확 일치로 고친 뒤 통과), dist 외부 URL 0.
- **테스트베드 실증(d17, 2026-11-03, 전 항목 PASS)**:
  - 같은 출발지에서 proof-a 오답 10회 → proof-a 맞는 비밀번호도 429 `login_rate_limited`(Retry-After 45), proof-b 는 200(다른 아이디가
    함께 잠기지 않음). 위조 `X-Real-IP: 6.6.6.6`·`X-Forwarded-For: 7.7.7.7` 은 기록되지 않음(client_ip = 10.10.10.1).
  - 관리자 재설정 200 `sessions_revoked` → 대상의 옛 세션 401 `session_revoked`, 옛 비밀번호 401·새 비밀번호 200, 자기 자신 409.
    브라우저로 로그인해 둔 대상은 로그인 화면에서 「비밀번호가 바뀌어 로그인이 끝났습니다」 안내.
  - 인증번호 6자리(stub), 전체 오답 상한을 env 3 으로 낮춰 발동 → 맞는 코드도 429 `verification_global_locked` → 관리자 전체 해제 →
    맞는 코드 200 → 상한 원복(1000).
  - 대기형 스위치(L=2·W=60·M=5): 동시 8개 중 6개 503 `login_busy`(Retry-After 30), 3.6초 대기 뒤 응답 1건, 성공은 토큰 환불.
    그동안 `/healthz` 328회 모두 200(최대 0.019s). 맞는 로그인 동시 30개 모두 200(최대 1.05s, scrypt 슬롯 2개).
  - 보안 이벤트 화면 1280·375, 재설정 창, 로그인 6자리 칸 — 콘솔 오류 0·가로 넘침 0. 375폭에서 이벤트 표가 옆으로 밀리던 것을
    좁은 화면 쌓기(같은 DOM, CSS 만)로 고쳐 d18 에 싣는다.

### ✅ ① B3 — ref 기록이 끊긴 틱의 잡 객체를 이어 받거나 회수한다(409 AlreadyExists 를 잡 실패로 접지 않음) — **완료·실증**(2026-11-03, d17)

설계 v6 9절 「① B3 세부」(사용자 승인).

**현상**: 잡 객체 이름이 결정적이고(`dms-{op}-{phase}-{job_id[:12]}`·`dms-preflight-…`) ref 는 create **뒤에** DB 에 남긴다(`stepper._record_ref`). 그 사이 DB 가 끊기면(장애조치·컨트롤러 재시작) 다음 틱이 같은 이름으로 다시 만들다 409 를 받았다. 그것이 submit_failed 로 접혀 잡은 FAILED/REJECTED 로 잘못 기록됐고, vcjob 은 추적 없이 돌았다(pod_gc 는 phase_refs 만 본다). rm·sync 라면 화면 밖에서 지우거나 덮어쓴다는 뜻이다. DB 스키마·사유 코드·RBAC 변경은 없다.

- **create 갈래**: 실 클라이언트는 409 를 `K8sAlreadyExists` 로, 반영됐는지 모르는 실패를 `K8sTransient` 로 올린다(`create_error_class`). 「모르는 실패」는 상태 코드 없음·0·429·5xx·OSError·urllib3 전송 오류다. 그 밖의 4xx 는 원문 그대로 submit_failed(종전).
- **채택**(`VolcanoExecutionAdapter._create_or_adopt`·`adoption_problem`): 다시 읽은 객체가 아래를 모두 만족하면 새로 만든 것과 **같은 ref** 를 돌려준다. 복합 preflight 는 파드별로 본다.
  - 라벨 `dms.io/job-id`(전체 id)·`dms.io/phase`·`dms.io/tool` 이 같다. preflight 파드에 tool 라벨을 새로 붙였다.
  - 지을 때 실은 지문 annotation `dms.io/spec-fingerprint` 가 같다. `manifest_fingerprint` v1 = apiVersion·kind·이름·ns·라벨·spec 의 정렬 JSON sha256, 컨테이너 image 만 뺀다.
  - 삭제 중이 아니다.
  - 이미지를 지문에서 빼는 근거: 채택이 정하는 것은 실행이 아니라 추적이다. 틱 사이에 포탈 릴리스가 끼어도 이미 도는 객체를 놓치지 않게 한다. 행 값(재클램프·경로·소유자)은 지문에 남는다.
  - 409 로 채택한 것만 `submit_adopted`(info, 포탈 단계 행 주석 「이전 제출이 만들고 기록하지 못한 객체」)로 남긴다. create 응답만 잃은 채택은 로그만 남긴다.
- **「모름」은 실패가 아니다** — `SubmitUndecided`: 잡은 이전 상태에 머물고 run_once 의 step_error 를 남기며 다음 틱에 다시 낸다.
  - 409 뒤 다시 읽기 실패: 상한 없이 재시도.
  - create 결과 모름: 다시 읽어 같은 판정을 하고, 응답만 잃은 create 는 같은 틱에 채택한다.
  - 다시 읽으니 없음·삭제 중: `SUBMIT_UNDECIDED_WINDOW_SECONDS`(300초)를 넘기면 종전 submit_failed.
  - 문구는 판정을 앞에 두고, apiserver 원문은 따로 잘라 끝에 붙인다.
- **채택하지 않은 이 잡의 객체는 회수한다**:
  - 라벨 셋은 같고 지문만 다른 객체(배포 경계의 옛 객체)와, 복합 preflight 뒤쪽이 실패할 때 앞쪽 파드는 `ExecutionError.strays` 로 올라온다. `stepper._fail_submit` 이 지우고, 그 결과를 submit_failed 이벤트·진단 로그에 싣는다(못 지우면 error 「추적 밖에서 돌 수 있음」).
  - 이미 끝난 실행 전 재점검 파드(exec_preflight)는 채택하지 않는다. `SubmitUndecided(strays)` 로 지우고(`_submit_phase`) 다음 틱에 다시 점검한다. 재점검은 첫 실행 관문 뒤의 결과여야 하는데, 기록이 끊긴 잡은 겹침 판정에서 재점검 전으로 보여 앞선 rm 이 먼저 돌 수 있기 때문이다.
  - stepper 가 성공 아닌 종단으로 끝낼 때마다(`_finalize` 한 곳 — fail-closed·배치 취소 정지·앞 단계 파드 소실·소유자 측정 거부) 라벨(전체 job_id)로 phase_refs 밖 객체를 찾아 지운다(`_reclaim_unrecorded`·`unrecorded_refs`, `unrecorded_reclaimed` 이벤트).
  - 잡·요청·배치 취소는 Cancelled 커밋 뒤 같은 조회로 끝낸다(`api.cancel._sweep_unrecorded`).
- **겹친 컨트롤러**(30초 리스를 넘는 틱)의 낡은 스냅숏은 아무것도 더 하지 않는다:
  - phase_refs 빈 자리 CAS(`data_jobs.record_phase_ref` "recorded/gone/taken" — `set_phase_ref` 는 그 래퍼)
  - 제출 뒤 전진 전이의 상태 CAS(`set_job_state(expected_state=)`, `stepper._advance` — Running→Preflight, 컨펌된 Executing→ConfirmPending 되돌림 방지)
  - 실패 전 확인(`_phase_taken`). 회수는 이 확인 뒤에만 한다.
  - Executing→Executing 자기 전이는 하나만 남는다.
- **테스트**: `tests/test_submit_adopt.py` 90건. 열 가지 제출 경로 채택(preflight·exec_preflight·scan/rm 실행·rm·dsync 미리보기·nsync 복합 4단계), 라벨·지문·삭제 중·사라짐·GET 실패·transient, 복합 파드별, 지문의 이미지 무관·결정성(해시 시드), stepper 기록 실패 다음 틱 채택(preflight·실행·미리보기·exec_preflight·반복), 회수(strays·종단·취소·재점검), 겹친 스냅숏 CAS, 저장소 CAS.

**설계 9절과 달라진 점(출하 전 사용자 확인 필요)** — 설계 문구는 「하나라도 어긋나면 지금처럼 실패합니다」다.
1. **삭제 중(deletionTimestamp)이면 실패가 아니라 최대 300초 재시도**한다. 다 지워지면 다음 틱의 create 가 새로 만든다.
2. **409 뒤 다시 읽기(GET) 실패는 상한 없이 재시도**한다(작업 명세는 「clear reason 과 함께 submit_failed」였다). 409 는 객체가 있다는 증거라, 실패로 접으면 B3 가 같은 장애에서 되살아난다. 폴링의 GET 실패도 같은 방식이다.
3. **라벨은 같고 지문만 다른 이 잡의 객체는 실패로 끝내면서 그 객체를 지운다**(새 파괴적 동작). 배포 경계에서 빌더 모양이 바뀌면 실제 rm·sync 실행 vcjob 이 도중에 끊길 수 있다. 지우지 않으면 잡은 FAILED 인데 객체는 추적 밖에서 끝까지 돈다.
4. 모든 조건이 맞아도 **이미 끝난 exec_preflight 파드는 채택하지 않고 지운 뒤 재점검**한다.
5. 설계에 없던 확장: **create 결과 모름(5xx·429·타임아웃)도 다시 읽어 같은 판정**을 한다.
6. 설계에 없던 삭제 경로: **성공 아닌 종단·취소 때 라벨(전체 job_id)로 ref 밖의 이 잡 객체를 지운다**.

**테스트베드 실증(d17, 2026-11-03, PASS)** — drain 으로 잡을 Pending 에 묶고 컨트롤러가 지을 preflight 파드를 같은 이름·라벨·지문으로
미리 만든 뒤(`_record_ref` 직전 끊김과 같은 모양) drain 을 풀었다:
- 그대로: 그 파드(uid 하나)를 채택 → Pending→Preflight→Running→Succeeded, `submit_adopted` 이벤트·요청 단계 행 주석.
- 지문 변조: Rejected `preflight_submit_failed:submit_failed`, 미리 만든 파드를 컨트롤러가 회수(payload `reclaimed`).
- 남의 라벨: Rejected, 그 객체는 건드리지 않음(종단 20초 뒤에도 같은 uid).

**남은 것**:
- 겹친 컨트롤러가 다른 쪽이 기록한 끝난 재점검을 만나면 그 틱은 `step_error`(SubmitUndecided)로 남는다(무해한 경보).
- 앞 틱 객체 + 이번 5xx 로 채택된 경우 `submit_adopted` 주석이 없다.
- 둘째 실행 관문(GATE_EXECUTION) 보류 뒤 재점검이 오래된 것은 기존 한계 그대로다.

### ✅ 사용량 분석 「소속별」 탭(2단계) — 권한 사용자·요청자·namespace·PV 이름·StorageClass·클러스터별 실 사용량 순위 — **완료·실증**(2026-10-30, d16)

사용자 결정: 「그래 구현시작해.」(2026-10-29 하위 메뉴 시안의 결정 1 — 「소속별」은 묶음별 합계를 한 번에 주는 서버 API 와 함께만 출하).
한 스토리지를 **누가·어느 PV 가 얼마나 쓰나**를 실 사용량 순위로 보이고, 행을 누르면 그 묶음의 필터를 건 경로별로 간다. DB 스키마·
인덱스·사유 코드·npm 의존성 변경 없음.

- **API** `GET /api/admin/usage/breakdown?storage=&by=`(require_admin, 읽기 전용 — `routes_usage.usage_breakdown`): by = user·requester·
  namespace·pvc·storage_class·cluster(그 밖 422). 생략하면 판정 창에 PV 안 대상이 있으면 user, 없으면 requester; PV 축을 명시했는데 PV 안
  대상이 0 이면 requester 로 적용한다(응답 `by` = 적용 값, `requested_by` = 받은 값). 판정 창·귀속은 `/facets` 와 한 벌(최근 스캔 순
  `_WINDOW_LIMIT`, `usage_attribution.attribute`), 묶음 키는 순수 함수 `group_keys`(경로별 필터 `matches` 와 같은 판정 — 무작위 대조
  테스트), 요청자 축은 새 질의 `DataJobsRepository.scan_requesters_by_target`(500개 IN, 고아 잡 제외 — 요청자 필터와 같은 판정).
- **불변식(오라클)**: 행 값 = 그 행의 `filter` 를 건 경로별 필터 합계(`storage-totals?<filter>`)와 같은 정의·같은 값 — 묶음 안 최상위
  대상만 더하고(`team ⊃ team/sub`), 실 사용량은 목록 열과 같은 값(리포트 못 읽으면 bytes_count), 파일 수·hot 은 요약. target_count 는
  `/facets` 옵션 수·`/targets` matched_count 와 같다. 요청자 축은 창이 넘치지 않을 때만 필터 합계와 같다(필터 합계의 요청자 경로는 그
  요청자 대상만의 창을 쓴다 — 넘치면 둘 다 「최근 스캔된 N개 기준」 부분 값).
- **응답**: 행마다 축 키 필드 + target_count·summed/nested_target_count·total_bytes·total_files(sum·known·unknown)·hot_ratio·시점 범위·
  `filter`(경로별 URL 키 — 포탈이 그대로 싣는다). 순위 = 실 사용량 내림차순(모름은 뒤) → 대상 수 → 키, 상한 `FACET_LIST_CAP` →
  `rows_truncated`. 덧붙임 `extras`: PV 축의 outside(`scope=outside`)·unknown(`scope=unknown`), 축 값이 없는 대상 none(권한 사용자 없는
  namespace·SC 없음·요청 기록 없는 scan 만 — 필터 null). 최상위에 scope 네 수·cluster_count·storage_total(비중 막대 분모 = 무필터 합계와
  같은 정의)·window_truncated·pending/unreadable/progressed(storage-totals 와 같은 읽기 예산·실패 기억 — `_read_top_summaries` 로 뽑아
  함께 쓴다, storage-totals 바이트 불변).
- **리뷰 반영**: 경로별 필터 파라미터 규칙 밖 키(빈 dms_user·제어문자·253자 초과 이름 — DB 가 신뢰 경계)의 행은 순위에 남기되 `filter`
  null(실으면 422 — 숨기면 조용한 누락), `/facets` 는 같은 술어(`routes_usage.filter_value_ok`)로 그 선택지를 뺀다(고르는 순간 422 이던
  선택지). 묶음 누적은 행에 싣는 칸만(`usage_metrics.GroupTotals`, StorageTotals 도 그 칸은 같은 누적기)·조상 판정은 창에서 한 번
  (`MaximalIndex`) — 단일 멤버 묶음 1만 개 +39.7 → +4.2MiB. 기록 pv 블록의 JSON 이스케이프 외톨이 서로게이트(`\ud800`)가 응답 인코딩에서
  500 이던 것(/facets·/targets 에 원래 있던 결함 — 소속별도 같은 입력에서 500)은 `parse_block` 이 무효 블록으로 본다.
- **비용**(sqlite, 한 스토리지 9,881 대상·잡 6.7만, PV 600·클러스터 둘·namespace 마다 3명, 캐시 데운 상태): user 0.62초 · pvc 0.64초 ·
  requester 1.39초(대상·요청자 쌍 5.1만) · namespace 마다 100명이면 user 0.94초. user 축을 필터 합계로 묶음마다 부르면 40회 ≈ 13초다.
- **포탈**: 세 번째 탭 「소속별」(`/admin/usage/breakdown`, 크럼 「소속별」 — 평평한 라우트). 경로별과 같은 스토리지 select(기본 스토리지·
  붙잡기·300ms push — 탭 링크가 경로별에서 보던 스토리지를 싣는다), 「기준」 select(`?by=` replace, 권한 사용자 = 생략) + 도움말, PV 안이
  0 이면 PV 축 다섯 disabled + 「이 스토리지에는 PV 안 대상이 없습니다 — 요청자 기준만 볼 수 있습니다」(`aria-describedby`), PV 안이 있으면
  「PV 안 n · PV 밖(직접 경로) n · 모름 n」(조각마다 경로별 scope 링크). 순위(`GroupTable` — ARIA 표, 1280 이상 표 · 그 아래 두 줄 행):
  이름(+ 부속 — PV 이름은 경로별 PV 이름 칸 표기 「namespace (SC) · 클러스터」) · 대상 · 실 사용량 + 비중 막대(스토리지 합계 대비 —
  100% 정규화 아님, 「≈100%」·「<0.1%」) · 파일 · hot. 행 전체가 경로별 링크(push, `?storage=&<filter>` 만), filter null 행·none 덧붙임은
  링크 아님(설명). 처음 100행 + 「모두 보기 (n개)」, 창 넘침이면 값 「이상」·대상 수 「+」·각주 「최근 스캔된 N개 기준」, 분모가 부분 합이면
  「X 이상 대비(근사)」, 요약 읽는 중 1초 이어 읽기(TotalsLine 관용), 옛 API(404) 안내, 메아리 불일치 오류, 미등록 스토리지 한 줄.
  각주: 「행끼리 더하지 않습니다 — 한 경로가 여러 사용자·요청자에 속합니다 · 묶음 합계 = 그 묶음에 맞는 경로 중 최상위 합 · …」.
- **테스트**: `test_api_usage_breakdown`(새 — 여섯 축·덧붙임·기본/대체 by·순위·상한·창 넘침·읽기 예산·422·권한·겹친 경로·**모든 스토리지 ×
  축의 행마다 필터 합계·facets·targets 대조**·규칙 밖 키·서로게이트), `test_usage_metrics`(MaximalIndex·GroupTotals 대조),
  `test_usage_attribution`(서로게이트 블록). 포탈 `BreakdownPage.test.tsx`(새, 30), `UsageHeader`·router·navigation, e2e
  `16-usage-breakdown`(새).
- **검증**: 전체 백엔드 4387 passed(스냅숏), 사용량 파일 PG 235, vitest 1794, tsc(앱·e2e), dist 외부 URL 0, e2e 31. e2e 첫 실행에서
  E16 이 계정 생성 500 으로 한 번 실패했다 — 하네스 sqlite 의 쓰기 락 승격 경합(`global-setup.ts` 주석의 하네스 한정 문제: 컨트롤러 쓰기
  버스트 중 read-then-write 트랜잭션, 135ms 즉시 실패)이고 단독·E15+E16·전체 재실행 모두 통과. 오라클(무작위 세계 — 행마다 필터 합계·
  facets·targets·첫 원리 대조, storage-totals 리팩터 전후 대조, 규칙 밖 키) sqlite·PG 불일치 0, 변이 10종 중 9종 검출(남은 1종은 PVC SC
  충돌 세계가 없어서), 리뷰 지적 17건 반영.
- **라이브 실증(d16, 소스 ab8f02b)**: 실 Chrome — 탭 「경로별 · 스토리지별 · 소속별」, k8s-pv-nfs 기본(권한 사용자) junwoo.song 3 · 981.6
  KiB ≈100% / alice 2 · 981.6 KiB(동률은 대상 수) / cocoa.song 1 · 22 B <0.1% / 흐린 「PV 밖(직접 경로)」 2 · 14 B → `scope=outside`, 요청자
  mason 4·alice 1, namespace team-a · dmsk8s / lab-y · dmsk8s2(클러스터 둘이라 접미), PV 이름 「datasets team-a (tb-nfs) · dmsk8s」,
  StorageClass tb-nfs·c2-nfs, 클러스터 dmsk8s·dmsk8s2, onefs·gpfs 각 축. cephfs-dms 에 `by=namespace` 딥링크 → 요청자 적용·PV 축 다섯
  disabled·안내 문장. 행 클릭 alice → `/admin/usage?storage=k8s-pv-nfs&user=alice`(경로 2개), datasets → `cluster&namespace&pvc` 필터
  (경로 2개), 뒤로 가기 → 소속별(by 그대로). 1440·1024·390 가로 넘침 0(390·1024 두 줄 행), 콘솔 오류 0. **라이브 오라클**: 네 스토리지 ×
  여섯 축의 행·덧붙임 39건을 `storage-totals?<filter>` 와 대조(대상 수·최상위 수·실 사용량·파일 수) 불일치 0.

### ✅ 사용량 분석 「경로별」 — PV 이름 열 + 경로·PV 이름 검색(일부 단어) — **완료·실증**(2026-10-29, d15)

사용자 요청: 「테이블에 경로 이름뿐 아니라 pv 이름도 표시해줘. 그리고 경로로 검색할 뿐 아니라, pv 이름도 검색할 수 있게 하고 일부
단어만 포함해도 뜨도록 해줘.」 「일부 단어만 포함해도」는 포탈의 기존 규칙(2026-10-25 작업 목록 검색, `text_search.search_tokens`)과
같은 뜻으로 맞췄다 — 공백으로 나눈 토큰마다 **부분** 일치, 토큰 사이 **AND**, 대소문자 무시, 최대 8토큰, NUL·화살표는 구분자.
DB 스키마·인덱스·사유 코드·npm 의존성 변경 없음.

- **API**: `GET /api/admin/usage/targets`·`/targets/export` 에 `q`(선택, ≤512자, NUL 은 422 — `_NO_NUL`). 한 대상은 **모든 토큰**이
  그 경로 또는 소속 PV 정체 하나의 PV 이름(= PVC 이름, inventory·recorded 모두)에 걸릴 때 남는다 — 토큰마다 어느 쪽이든(「models run」 =
  PV 이름 models 이고 경로에 run). namespace·클러스터·StorageClass 는 검색하지 않는다(그건 필터다). 역시 **대상 단위**(scan_count·latest
  는 그 대상의 전체 기록). 빈 값·공백뿐 = 검색 없음. `filters` 메아리에 `q`(받은 원문, 빈 문자열은 null — path 와 같다)를 더했다. 옛
  `path`(방언 LIKE 그대로)는 옛 탭 호환으로 남고 q 와 함께 걸린다. `/facets`·`storage-totals` 는 q 를 받지 않는다(합계는 경로 검색과
  무관 — 2026-10-21 결정).
- **실행 두 길**(`routes_usage._select_heads`): PV 이름이 생길 수 있는 스토리지(`usage_attribution.may_attribute_pv` — PV 를 담는
  종류의 등록 스토리지 **또는 PV 주소 scan 기록이 있는 스토리지**)는 PV 축 필터와 같은 판정 창(정렬과 무관한 최근 스캔 순
  `_WINDOW_LIMIT + 1`)에서 소속을 판정한 뒤 `usage_attribution.search_matches`(순수 — 대상·토큰 양쪽 같은 `str.lower()`)로 거른다
  (넘치면 matched_count null·window_truncated). 그 밖은 SQL(`DataJobsRepository.scan_target_heads(tokens=)` — 토큰마다
  `lower(target) LIKE lower(:qtN) ESCAPE '\'`, 패턴은 `contains_pattern`)로 LIMIT 앞에서 정확히 건다(창 없음). PV 축 필터와 함께여도
  PV 이름이 생길 수 없으면 토큰은 SQL 에 실린다. 공용 술어 `_scan_target_where` 는 바꾸지 않았다(batches·동일성 참조가 쓴다) —
  tokens 가 없거나 빈 목록이면 머리 문장이 예전과 글자 하나 다르지 않다.
- **창이 넘쳐도 경로 일치는 창 밖까지**(리뷰 반영, `routes_usage._with_path_hits`): 포탈이 path 대신 q 를 보내게 되면서 PV 를 담는
  스토리지의 대상이 판정 창(1만)을 넘으면 경로로 찾던 오래된 대상이 창 밖이라 안 걸렸다(예전 path 는 SQL 로 정확 — 「경로로 검색할 뿐
  아니라」의 회귀). 이제 창이 넘치면 **모든 토큰이 경로에 든 대상**을 `scan_target_heads(tokens=)` 로 창과 무관하게 더한다 — PV 축이
  없고 정렬이 last_scan·scans 면 요청한 정렬 그대로 limit 개를 SQL 로 읽어 창 안 결과와 문자열 비교 없이 병합(`_merge_outside` — 창은
  최근 스캔 순의 앞부분이라 앞뒤가 정수·시각 같음만으로 정해진다), PV 축이 있거나 지표 정렬이면 경로 일치끼리의 최근 창(예전 「path +
  PV 필터」의 창)에서 소속을 판정해 이어 붙인다. 그래서 PV 이름으로만 맞는 대상이 없는 검색은 결과·순서가 예전 path 와 같다(정렬 다섯 ×
  방향 × 상한을 대조하는 테스트). matched_count 는 여전히 null 이다(창 밖의 PV 이름 일치는 모른다).
- **내보내기의 상한 잘림을 창 넘침과 따로**(2차 리뷰 반영): 창 밖 경로 일치를 더하면서 PV 경로의 내보내기도 상한(1만)에서 **알려진**
  일치를 자를 수 있게 됐다(예전엔 PV 경로 행 ≤ 창 = 상한이라 잘릴 일이 없었다). 그런데 봉투는 창 넘침과 같은 모양(truncated true,
  matched_count null)이라 포탈이 「PV 이름으로 맞는 대상은 최근 1만 개 안에서만 찾았습니다」만 냈고, 같은 화면 문단이 「경로는 범위
  밖까지 찾는다」고 하니 잘린 경로 일치가 안 보였다. `/targets/export` 봉투에 `capped`(truncated 다음 — `{generated_at, truncated,
  capped, matched_count, filters, rows, count}`)를 더했다: 알려진 맞는 대상이 상한보다 많아 잘랐다 — SQL 경로는 matched_count > 상한
  (truncated 와 같다), PV 경로는 판정한 결과가 상한을 넘음(`_select_heads` 가 함께 돌려준다). 경로 일치는 하나 더 읽어(정렬 갈래
  limit + 1, 지표·PV 축 갈래는 경로 일치끼리의 창 + 1 — 창 자체는 그대로) 행 수가 꼭 상한인 경우도 가른다; PV 축이 있으면 경로 일치끼리의
  창 밖은 판정하지 않은 것(모름)이라 잘림으로 세지 않는다. 포탈은 capped 면 「상한 N개까지만 내보냈습니다. (창 문구) — 검색·필터로
  범위를 좁히세요」, 아니면 창 문구만 낸다 — 행 수가 상한과 같다는 것만으로는 「잘렸다」고 하지 않는다(창 안 결과와 경로 일치를 합한 것이
  꼭 상한만큼일 수 있다). truncated 의 뜻(행이 전부가 아닐 수 있다 = 창 넘침 또는 capped)과 필터 합계의
  truncated 는 그대로다.
- **알려진 한계**: 대상이 1만(`_WINDOW_LIMIT`)을 넘는 PV 스토리지에서 **PV 이름에만 맞는**(토큰 하나라도 경로에 없는) 대상은 최근 스캔된
  1만 개 안에서만 찾는다 — 화면 문단·CSV 문구·빈 상태가 그렇게 말한다. 없애는 길은 PV 축 필터와 같은 SQL 푸시다운(BACKLOG 「사용량 분석
  필터 후속」).
- **계약에서 넓힌 한 가지**: 처음 계약은 「filesystem·미등록 = PV 이름 없음 → SQL」이었지만 기록 소속은 종류·등록과 무관하게 생긴다
  (`collect` 는 종류를 보지 않고 PV 주소 scan 기록을 묻는다 — 지운 스토리지·종류를 바꾼 스토리지의 옛 PV 주소 scan). 그대로 두면 표에
  보이는 기록 PV 이름으로 검색해도 그 행이 안 걸린다 — 그래서 문턱을 「PV 종류 등록 ∪ 기록 있음」으로 잡았다(문턱이 False 면 모든
  판정의 volumes 가 비어 SQL 경로와 결과가 같다 — 단위 테스트가 고정).
- **포탈**: 검색칸 값을 path 대신 q 로 보내고(URL 키 q 그대로, CSV 요청도 q 만) 메아리 대조에 q 를 쓴다(옛 서버 — 메아리에 q 없음 —
  면 기존 「필터가 화면과 다릅니다」). 라벨 「경로·PV 이름 검색」·placeholder 끝 「…」, 규칙 「여러 단어는 모두 들어간 것만 · 대소문자 무시 ·
  경로나 PV 이름 어디든」은 입력칸 `aria-describedby` 와 HelpTip 둘 다, 입력 `maxLength` 512(서버 422 전에). 표는 「경로」 다음 「PV 이름」
  열(`TERMS.pvc`, 정렬 없음 — `UsageParts.PvName`: PVC 이름 + 작게 「namespace (StorageClass)」·여러 클러스터면 「 · 클러스터」, 「외 n개」,
  「기록」, 흐린 「PV 밖」, 기울인 「모름」, 소속이 없는 행은 「—」), 경로 칸 둘째 줄(PV 소속)은 지웠다. 열은 선택지(facets)의 PV 안 대상이
  0이고 받은 행에도 없으면 숨는다(filesystem·미등록 6열). 강조는 새 `components/ui/Highlight`(서버 `search_tokens` 와 같은 토큰 규칙, 겹침
  병합, React 노드 `<mark>` — `dangerouslySetInnerHTML` 없음)로 경로·PV 이름 양쪽, 자리표시 동안은 그 행을 낸 검색(메아리 q)으로. 계약에서
  더한 것: 경로 버튼에 `aria-label`(경로 그대로 — `<mark>` 가 접근 이름을 쪼개지 않게), 루트 충돌 행은 검색 토큰에 가장 많이 맞는 PV
  이름을 칸에 세운다(title 에 정체 전부). 판정 창이 넘친 결과의 말(`StoragePanel`): PV 필터만이면 「최근 스캔된 N개 대상 안에서 k개 맞음 —
  … PV 필터는 이 범위에서만 거릅니다. 검색으로 범위를 좁히세요」, 검색이 끼면 「k개 맞음 — … PV 이름으로 맞는 대상은 … 안에서만
  찾습니다. 검색어가 모두 경로에 든 대상은 범위 밖까지 찾습니다」(PV 필터와 함께면 「PV 필터와 PV 이름 검색은 … 안에서만 판정합니다.
  검색어가 모두 경로에 든 대상은 그것끼리 최근 스캔된 N개 안에서 PV 필터로 거릅니다」), 0건이면 「… 안에는 ‘x’ 검색 결과가 없습니다」 +
  「검색어가 모두 경로에 든 대상은 범위 밖까지 찾았습니다」, CSV 잘림 문구도 같은 주어(상한에서도 잘렸으면 — 봉투 capped — 앞에
  「상한 N개까지만 내보냈습니다.」). 요청자 필터는 권하지 않는다(선택지가 하나면 그
  칸이 없다 — 리뷰). 「경로 검색」이라 부르던 문구(합계 줄 「검색은 합계에 들어가지 않습니다」·합계 도움말 「검색(경로·PV 이름)은 반영하지
  않습니다」·CSV·빈 상태)는 「검색」으로.
- **테스트**: `test_usage_attribution`(토큰 판정 — 경로만·PV 이름만·섞인 토큰 AND·대소문자·기록 PV 이름·PV 밖·와일드카드 없음·신뢰 경계
  값, 문턱 7경우), `test_repo_data_jobs`(SQL 토큰 — 부분·대소문자·AND·`% _ \` 리터럴·파티션 무손상·matched_count·기본 인자 문장 무변경),
  `test_api_usage_analysis`(테스트베드 모양 세계 — onefs·gpfs 의 PV 이름만 맞는 대상, 섞인 토큰, 기록 PV 이름이 filesystem·미등록
  스토리지에서도·클러스터를 지운 뒤 경로에 없는 PV 이름(kraken-kv-cache)으로, SQL 경로는 창 1 에서도 정확·PV 경로는 창 넘침 null, 창 밖
  경로 일치가 예전 path 와 같은 결과·순서(창 경계의 같은 시각 묶음·창 밖이 스캔 횟수가 더 많은 병합 모서리, PV 필터와 함께, 내보내기),
  PV 이름에만 맞는 대상은 창 안만, path·요청자·PV 필터와 결합, 두 경로의 와일드카드 리터럴, NUL·513자 422, 내보내기 메아리·truncated·
  capped(정렬 다섯 × 방향, 행 수가 꼭 상한인 경우, PV 필터와 함께면 잘림 아님, 창 안 PV 이름 일치 + 창 밖 경로 일치), facets·합계는 q 무시), `test_migrations`(토큰 머리 질의도 `idx_data_jobs_scan_target`). `test_usage_equivalence` 무수정 통과(sqlite·PG).
  포탈 vitest: `Highlight.test.tsx`(새 — 토큰 규칙·겹침·대소문자), `UsageAnalysis.test.tsx`(7열 표·검색 AND·대소문자·양쪽 강조, 선택지 실패
  시 PV 열, 옛 서버 메아리, 창 넘침 문구 — 검색만·PV 필터만·둘 다·0건·CSV(capped 면 「상한 N개까지만」을 함께, 아니면 행 수가 상한이어도
  창 문구만), 화면에 없는 요청자 필터를 권하지 않음), `TargetSheet.test.tsx`
  (라벨). e2e `15-usage-filters` 9) 검색 단계(대문자 두 단어 AND·URL q·`<mark>`·filesystem 이라 PV 이름 열 없음·지우면 복원)를 더했다.
- **검증**: 전체 백엔드 4352 passed(스냅숏) + 사용량 파일 최종 sqlite 295·PG 252, vitest 1759, tsc(앱·e2e), e2e 30, dist 외부 URL 0.
  리뷰 2라운드 — 무차별 대입 오라클(q·창 넘침 병합·capped) sqlite 1,872세계·PG 276세계 불일치 0(변이 7종 모두 검출), 지적 6건 반영.
- **라이브 실증(d15, 소스 4934968)**: 실 Chrome — PV 스토리지 표 열 「경로 · PV 이름 · 실 사용량 · …」(cephfs-dms 는 PV 열 없이 6열),
  nfs `data` → `team-a/datasets/proof-scan`·`dms-proof`(경로·PV 이름 양쪽 강조), `WEIGHTS` → `c2/lab-y/weights`(대소문자 무시),
  `team proof` → 2행(AND)·`team weights` → 0건, onefs `kraken` → `data/csi/k8s-3fa9c2d71e/dms-proof`(경로에 없는 PV 이름 `kraken-kv-cache`
  로 찾음·강조), gpfs `models` → `pvc-52c6…/dms-proof`(PV 이름), cephfs `GROW` → `e2e/growth`. 1440·390 가로 넘침 0, 콘솔 오류 0.

### ✅ 사용량 분석 하위 메뉴 — 경로별 · 스토리지별 + 경로 상세 시트 + 합계 줄 — **완료·실증**(2026-10-29, d14)

사용자 결정(2026-10-29, 시안 검토 뒤 「권장사항대로 구현 진행」): ① 하위 메뉴 1단계는 「경로별」(기본, `/admin/usage`) ·
「스토리지별」(`/admin/usage/storages`) 둘 — 「소속별」(권한 사용자·요청자·namespace·PV 별 합계)은 서버 `/breakdown` 과 함께 2단계로만
(기존 API 로는 「누가 많이 쓰나」 순위가 틀린다), ② 경로 상세는 표 행 펼침 대신 오른쪽 모달 시트(↑↓ 이웃 경로), ③ 들어오는 화면 =
경로별(열 때 요청이 예전과 같다 — 스토리지별은 스토리지 수만큼 합계를 부른다), ④ 하위 메뉴 = 화면 안 탭(K8sTabs·BuildTabs 관용, 사이드바는
「사용량 분석」 하나). 블록 9개가 세로로 쌓이던 화면(1440 필터 + 펼침 2,264px)을 스토리지 탭 줄 → select, 합계 두 벌 → 한 줄, 행 펼침 →
시트, 「스토리지별 비교」 → 탭으로 줄였다. **백엔드·DB·npm 의존성 변경 없음**(기존 API 만).

- **하위 탭**(`UsageHeader.tsx` 의 `UsageTabs`·`UsageHeader` — 파일 이름이 `usageTabs.ts` 와 대소문자만 다르면 대소문자 무시
  파일시스템에서 모듈 해석이 엇갈려 피했다): 평평한 라우트 둘(레이아웃 라우트 없음 — `ErrorBoundary key={pathname}` 그대로, 정리 결과 같은
  상태는 경로별 페이지가 소유), `DETAIL_ROUTES` 에 「스토리지별」(크럼 `… > 사용량 분석 > 스토리지별`, 사이드바는 둘 다 사용량 분석).
  「경로별」 탭 링크는 마지막 경로별 쿼리(target 제외)를 sessionStorage(`usageMemory`, try/catch — 막히면 쿼리 없는 링크)에서 복원한다
  — 기본 스토리지를 보고 있었으면 기억한 스토리지를 `storage=` 로 적어서(붙잡은 기본은 페이지 상태라 탭 왕복에 사라지고, 그 사이 요약이
  바뀌면 다른 스토리지가 열렸다).
- **경로별**: 범위 줄 = 네이티브 스토리지 select(optgroup 「등록」 | 「미등록 — 기록만 남음」, 옵션 글자에 타입·대상 수 — 요약 전엔 수
  없이, 300ms 디바운스 뒤 push — select 를 떠나면(blur) 바로 반영해 디바운스 창 안의 옛 스토리지 행·CSV·정렬이 다른 스토리지에 걸리지
  않는다, 기다리던 값은 라우터의 위치 전환과 같은 전환으로 거둬 반영 순간 옛 값으로 되돌아가지 않는다, target 은 버리고 필터·q·sort·order
  는 남김, 이름 툴팁은 select 가 아닌 감싼 칸 — 접근 설명이 되지 않게) + 메타 줄(이름 항목은 뺐다). 기본 스토리지 파생·붙잡기·
  없는 storage 정리(replace + 「기본 스토리지를 보여 줍니다」 — 뒤로·앞으로 가기로 집합 안의 storage 로 돌아오면 거둔다)는
  `useActiveStorage` 로 뽑았다. 미등록 경고 띠·정리 확인 창·결과 줄·포커스
  규칙은 그대로(결과 줄은 하위 탭 아래 알림 자리). 경로 검색(q)·정렬(sort·order, 기본값 생략)도 URL(replace)로 올렸다. 도구 줄은
  [검색 20rem][건수] … [CSV] 아래 CSV 결과 한 줄, 375 는 [검색][CSV 아이콘] / 건수. 필터 select 상한 12rem. hot 도움말에 「낮은 순 = 정리
  후보」. 바깥 패널 카드를 없앴다(표 `Table framed`).
- **경로 상세 시트**(`TargetSheet`, `Dialog side="right"` — md 이상 오른쪽 640px, 그 아래 전체 화면, 150ms 슬라이드·움직임 줄이기면
  없음): 머리 = 경로(등폭·한 번에 전체 선택, 복사 버튼 없음) · 「스토리지 · 목록 i/n번째」 · 이전/다음/닫기, 본문 = TargetDetail 그대로.
  열기 push(+ `location.state` 표지), ↑↓ replace, 닫기는 표지가 있으면 뒤로(-1)·없으면(옛 딥링크) target 만 지우는 replace, 시트 안
  PV 이름·권한 사용자 = 필터 걸기 + 닫기 한 번의 replace, 지금 인벤토리 PV 엔 「PV 탐색에서 보기」. 목록 밖 경로는 시트가 「지금 목록 밖」
  한 줄로 말한다(예전 폴백 칸 제거). 이전·다음은 끝에서 `aria-disabled`(disabled 면 실 브라우저가 포커스를 <body> 로 떨어뜨렸다)이고
  옮긴 결과는 sr-only 상태 줄이 「경로 · 목록 i/n번째」로 알리고 2초 뒤 비운다(읽기 모드에서 제목·부제와 두 번 읽히지 않게). 닫기는
  이전·다음 묶음과 12px 띄웠다(버튼은 포탈 관용 36px — WCAG 2.5.8 24px 이상). 시트 안 도움말(?)이 열린 채 Esc 는 툴팁만 닫는다(`useTip` 이 window
  캡처 단계에서 받는다 — 예전엔 Radix 가 먼저 받아 시트째 닫고 뒤로 갔다).
- **합계 줄**(`TotalsLine`): 「이 스토리지 합계」·「필터 합계」 두 블록을 표 위 한 줄 dl(실 사용량·파일·디렉터리·hot·df %(사용/전체),
  df 90%↑ 주의색, 부분 합 「이상」, 인코딩 경고, 읽는 중, 실패 + 다시 시도) + [자세히](기본 닫힘 — 안은 예전 StorageTotals 블록, 필터면
  「이 스토리지 전체」 한 줄)로. 같은 쿼리 키라 요청 수는 같다. 필터를 건 동안 스토리지 합계의 실패·[다시 시도](「이 스토리지 합계
  다시 시도」)·df 90%↑ 주의(「이 스토리지 df」)는 줄이, 노드 불일치·인코딩 경고·읽는 중은 전체 한 줄이 말한다. [다시 시도]가 성공해
  단추가 사라지면 포커스는 줄(구역)로 옮긴다(<body> 로 떨어지지 않게). 줄어든 것: 필터를 건 동안 스토리지 전체 분포를 나란히 못 보고,
  전체 각주는 「이상」으로만 남는다.
- **스토리지별**(`StoragesPage`): 사실 한 줄(등록 · scan 대상 · 성공 scan · 미등록 — 모르는 수는 뺀다) + 예전 「스토리지별 비교」 표
  (`UsageOverview.CompareTable` — `<details>` 래퍼 제거)에 최근 스캔 열과 클라이언트 열 정렬(실 사용량·df 사용률·대상·최근 스캔, 값 모름은
  맨 뒤, 「스토리지」 머리 = 기본 순서 등록 → 구분선 → 미등록 — 다른 열과 같은 아이콘 문법: 기본이면 ↑, 정렬 중이면 흐린 ↕)을 더했다.
  각주에 최근 스캔 경과 색 범례(30일↑ · 90일↑ — 경로별 각주와 같은 조각). 합계는 `useQueries`(`storageTotalsOptions` — 경로별 합계 줄과
  같은 키). 이름(또는 행) → `/admin/usage?storage=X`(push), 마지막으로 본 경로별 스토리지 행은 aria-current·bg-infobg.
- **테스트**: `UsageAnalysis.test`(1,767줄)를 화면별로 나눴다 — 경로별 `UsageAnalysis.test` · 시트 `TargetSheet.test` · 스토리지별
  `StoragesPage.test` + 공용 `usageTestKit`, 부품 `StorageSelect.test`·`UsageHeader.test`, router.test(h1 ↔ 탭 짝)·navigation.test. 기존
  단언(기본 파생·붙잡기·필터 replace·건수·창 문구·정리 포커스·옛 딥링크 → 시트)은 옮겨 살렸고, 탭 전용 키보드 테스트(수동 활성)는
  select 디바운스 테스트로 바꿨다. 쓰는 곳이 없어진 공용 `components/ui/Tabs`(+ 테스트)는 지웠다(화면 안 하위 메뉴는 NavLink
  관용 하나). e2e 03(경로별·스토리지별 1280·375 L1~L4)·09(스토리지별 미등록 행 → 경로별 select → 시트 폭)·
  15(탭 왕복 필터 복원)를 새 화면에 맞췄다(스토리지별 표는 합계 칸 자리표시가 걷힌 뒤에 잰다). 09 의 미등록 행 찾기는 `filter({ has })`
  안쪽 locator 를 행 기준으로 고쳤다(`page.getByRole("table")` 로 시작해 「행 안의 표」를 찾아 늘 0건이던 테스트 결함).
- **검증**: vitest 1747, tsc(앱·e2e), e2e 30, dist 외부 URL 0, 백엔드·npm 변경 없음. 리뷰 2라운드(4+3관점) 지적 21건 반영 — 시트 안
  도움말이 열린 채 Esc 가 시트째 닫음(HelpTip 이 캡처 단계에서 받음), 끝 버튼 disabled 로 포커스 이탈(aria-disabled), select 반영 순간 옛
  값이 한 번 그려짐(startTransition), 탭 왕복 뒤 기본 스토리지 바뀜(마지막 스토리지 복원), 없는 storage 안내가 앞으로 가기 뒤에도 남음,
  [다시 시도] 성공 뒤 포커스 이탈 등. 경로별 설명은 탭 이름과 겹치지 않게 「고른 스토리지의 경로마다 scan 이력」.
- **라이브 실증(d13 → d14, 소스 d95a840·a6e68f2)**: 실 Chrome — 사이드바 진입 = 경로별(현재 탭 aria-current), 스토리지 select 옵션 6개
  (대상 수), nfs 로 바꾸면 URL push·문서 높이 1000px(예전 필터 + 펼침 2,264px), 합계 한 줄 「981.7 KiB · 파일 15개 · 디렉터리 6개 ·
  hot 100% · df 86% · 잘못된 인코딩 이름 3개 이상」·[자세히]로 분포. 시트: 열기 push(history 3→4)·1440 에서 x=800 w=640·포커스 h2·
  「목록 3/5번째」, 다음 경로 replace(4→4), 닫기 → 그 행 경로 버튼으로 포커스, PV 소속 → cluster·namespace·pvc 필터 + 닫힘. 스토리지별:
  「등록 6개 · scan 대상 11개 · 성공 scan 13건」, 실 사용량 정렬(cephfs-dms → nfs → onefs → gpfs → third → secondary), 이름 → 그 스토리지의
  경로별. 경로별 탭 링크가 마지막 쿼리(스토리지 + 필터) 복원, 옛 딥링크 `?storage&target` → 시트 열린 채·닫기 replace(history 그대로).
  1440·390 가로 넘침 0, 390 시트 전체 화면(390×844), 콘솔 오류 0. d13 실화면에서 select 일곱이 한 줄을 채워 필터 도움말 「?」 하나만
  둘째 줄로 떨어져 「?」를 필터 줄 맨 앞으로 옮겼다(d14).
- **남은 것**: 2단계 「소속별」(권한 사용자·요청자·namespace·PV 이름 순위) + 읽기 API `/api/admin/usage/breakdown` — 결정 1 에 따라 아직
  짓지 않았다. → 2026-10-30 출하(위 「소속별」 항목).

### ✅ 사용량 분석 필터 — 사용자별(권한 사용자·요청자)·PV 이름·namespace·클러스터·StorageClass·구분 — **완료·실증**(2026-10-29, d12)

사용자 요청: 사용량 분석을 사용자별·PV 이름·namespace 등으로 거를 수 있게. 두 설계안(A: 계획 시점에 귀속을 data_jobs 에 기록 /
B: 읽을 때 인벤토리로 해석)을 심사해 **하이브리드**로 지었다 — 의미·원천은 B(대상 단위, 「지금 인벤토리 → 기록 → 밖/모름」, 스키마·planner
무변경), API·UI 모양은 A(새 봉투 엔드포인트·filters 메아리·matched_count·facets·URL 상태). A 를 기각한 이유: planner·`recheck_request`(보안
재확인) 반환형 변경, data_jobs 8칸 + 백필, 그리고 백필이 옛 직접 경로 scan 을 「모름」으로 굳힌다(테스트베드 성공 scan 13건 중 6건이 PV
블록 없는 직접 경로 — 작업 지시의 「직접 경로 scan 은 없음」은 사실이 아니었다).

- **의미 — 모든 필터는 대상 단위**: 한 (storage, target)의 성공 scan 전부가 함께 걸리거나 빠진다(scan_count·first/last·latest·이력은 늘
  전체 기록 — ARCHITECTURE §5 새 불변식 「사용량 필터는 파티션을 자르지 않는다」). PV 소속(`usage_attribution`, 새 순수 모듈): 지금
  인벤토리에서 대상을 덮는 가장 깊은 PV 루트의 PVC 정체들(`K8sRepository.pv_root_index` — `pv_roots` 의 계산·메모를 옮기고 루트마다 정체를
  함께 둔다; `pv_roots` 는 그 키의 정렬과 바이트 단위로 같다 — 직접 경로 소유 판정이 쓰는 보안 경로라 등가 단언을 더했다) > 그 대상을 PV
  주소로 scan 한 요청의 유효한 최신 pv 블록(지운 PVC·클러스터) > 모름(미등록·PV 스토리지인데 매핑 없음·미적용 클러스터 — null ≠ 0) /
  PV 밖(PV 루트의 상위 디렉터리 포함). 권한 사용자 = 소속 PVC namespace 의 지금 인식된 RoleBinding(면제 운영자 없음). PV 축은 같은 정체
  하나가 모두 만족해야 하고, 축이 있으면 구분은 pv 로 본다(outside·unknown 과 함께면 0건).
- **요청자**는 SQL(`scan_target_heads(requester=)` — 비상관 서브쿼리 표식을 파티션 창 MAX 로 달고 바깥에서 거른다, `idx_requests_requester`)
  이라 창과 무관하게 정확하다. 요청 행 없는 고아 잡은 이 필터에서만 빠진다. **PV 축**은 **정렬과 무관한 최근 스캔 순** 머리 창(1만 + 1 —
  `/facets`·필터 합계·내보내기와 같은 창)을 **순서를 보존하며** 거르고 그 안에서 요청한 정렬로 놓는다(`routes_usage._select_heads`·
  `_window_order` — 문자열 비교 없이 SQL 순서를 물려받는다: scans 는 정수 안정 정렬, 오래된 순은 같은 시각 묶음째 뒤집기 — 동률·콜레이션
  까지 SQL 결과와 같다). 창이 넘치지 않으면 전역적으로 정확하고, 넘치면 matched_count 를 null(모름)·window_truncated 로 말한다. 스펙은
  처음에 「요청한 정렬로 창을 자른다」였으나 리뷰가 뒤집었다: 오래된 순·스캔 횟수에서 창이 가장 오래된·가장 많이 스캔된 1만 개가 돼
  정렬만 바꿔도 걸리는 집합이 바뀌고, facets 의 옵션 수·포탈·CSV 의 「최근 스캔된 N개 안에서」가 거짓이 됐다.
- **API**: `GET /api/admin/usage/targets`(봉투 `{rows[+pv], matched_count, window_limit, window_truncated, filters}` — 행마다 소속 pv 를
  필터가 없어도 싣는다), `GET /targets/export`(`{generated_at, truncated, matched_count, filters, rows[…, pv], count}` 스트리밍 — truncated 는
  첫 바이트 전), `GET /facets?storage=`(구분별 대상 수·클러스터·namespace·PVC(정체 (cluster, namespace, pvc) 마다 한 줄)·StorageClass·
  권한 사용자 선택지(머리 창 기준) + 요청자(스토리지 전체 기준), 목록 상한 2000·lists_truncated), `storage-totals` 의 선택 필터(path 는 받지
  않는다 — 10-21 「합계는 경로 검색과 무관」; 걸러진 집합 안의 최상위만, filesystem null, filters 메아리; 필터가 없으면 요청·응답이 예전
  그대로). 새 필터 값의 빈 문자열·제어문자·나쁜 scope 는 422(「필터 없음」으로 접지 않는다 — PG NUL 500 도 막힌다). 2차 리뷰 뒤: 새
  엔드포인트(`/targets`·`/targets/export`·`/facets`·`storage-totals`)의 storage·path 에 든 NUL 도 422(PG 500·sqlite 빈 결과로 방언마다
  달랐다 — 빈 문자열·다른 제어문자는 이름이라 받는다, `_NO_NUL`), 권한 사용자 필터 상한 128 → 253·요청자 128 → 512(facets 가 내는 값을
  같은 API 가 422 로 거절했다 — 바인딩 이름(≤253)에서 파생한 dms_user 는 최대 236자, 옛 공유 토큰 제출의 요청자 `node:<노드 이름>` 은
  259자). 새 사유 코드 없음.
  **옛 `scan-targets`·`export` 는 동결**(본문 무변경 — `test_usage_equivalence` 무수정 통과, 제거는 BACKLOG).
- **포탈**: 패널 툴바 아래 **필터 줄**(`UsageFilters` — 클러스터 → namespace → PV 이름 → StorageClass → 구분 → 권한 사용자 → 요청자 select
  + 「필터 지우기」, 라벨은 `k8sTerms.TERMS` 만, 옵션마다 「· N개」, 위계(클러스터를 바꾸면 namespace·PV 이름이 풀리고 아래 단계 선택지는
  위 단계로 좁혀진다, 「PV 밖」·「모름」과 PV 축은 서로 푼다), 선택지가 둘 이상이거나 켜져 있을 때만 보이고 PV 안 대상이 0 인 탭은
  요청자만, 375 에서 두 칸씩 접힌다). 필터는 **URL 상태**(`cluster·namespace·pvc·storage_class·user·scope·requester` — PV 탐색과 같은 키,
  `useUsage.ATTR_KEYS`): 탭 전환·행 펼침·잘못된 storage 정리가 지금 파라미터에서 바꿀 키만 고친다(필터는 탭을 바꿔도 남는다), 필터 변경은
  replace. 그 스토리지 선택지에 없는 값은 보조 라벨로 풀 수 있게 남는다(「(이 스토리지에 없음)」 — 리뷰 뒤: 짝이 덜 찬 값은 비어 있는
  축을 「(모든 클러스터)」·「(모든 namespace)」·「(모든 클러스터·namespace)」로, 이름은 있는데 고른 짝 아래에만 없으면 「(이 클러스터에
  없음)」·「(이 namespace 에 없음)」, 판정 창이 넘친 스토리지의 PV 값은 「(최근 N개 안에 없음)」 + 「PV 선택지와 수는 최근 스캔된 N개
  기준」 한 줄). 건수·각주는 봉투의 matched_count 로(「N개」·「상위 100개 표시 · 전체 N개」·「필터에 맞는 N개」 — 탭의 대상 수가 아니다),
  모름이면 「최근 스캔된 N개 대상 안에서 k개 맞음 — …」 한 문단(리뷰 뒤: 요청자·경로 검색이 SQL 로 먼저 좁힌 것을 밝히고, 지표 정렬
  각주는 「창 → 필터」 순서로 「… 안에서 PV 필터에 맞는 것끼리 정렬」, 0건은 「그 범위 안에는 없습니다」, CSV 문구도 같은 범위 말).
  필터 0건이면 빈 상태 「필터에 맞는 대상이 없습니다」 + 「필터 지우기」(툴바는 남는다). **배포 틈**은 포탈만 판정한다(`UsageContractError`
  — 봉투가 아니면 「서버가 새 사용량 API 를 아직 모릅니다」, 메아리가 다르면 「서버가 적용한 필터가 화면과 다릅니다」, 둘 다 CSV 를 막는다;
  서버 사유 코드가 아니라 `reasonCodes.json` 무변경). 표는 6열 그대로 경로 칸 둘째 줄에 소속(`PV 이름 — namespace (SC)` · 클러스터 ·
  「외 n개」, 기록이면 「기록」 태그, PV 밖·모름), 펼친 상세 머리에 PV 소속·권한 사용자(요청자 필터면 「이력은 전체 이력」). 필터가 켜지면
  스토리지 합계 아래 **필터 합계** 카드(df 없음, 경로 검색 무시, 적용 필터 한 줄). CSV 는 `k8s_source·cluster·namespace·pvc·volume·
  storage_class·pv_root·access_users` 8열을 더하고(여러 값은 `;`, 모름은 빈 칸) 필터가 걸리면 파일 이름에 `-filtered`. 리뷰 뒤 포커스:
  「필터 지우기」(줄·빈 상태)나 값 때문에만 보이던 칸이 사라지면 포커스를 필터 줄(group, tabIndex -1 — 없으면 툴바)로 옮긴다(<body> 로
  떨어지면 화면 낭독기가 자리를 잃는다 — JobsList 와 같은 유형). 2차 리뷰 뒤: 툴바도 없으면 패널 자체(tabIndex -1)로, 그리고 새 필터의
  목록이 자리 잡을 때까지 이 확인을 되풀이한다(기록 0 탭에서 지우면 응답 전엔 있던 툴바가 「기록 없음」 응답과 함께 사라져 옮겨 둔
  포커스가 다시 <body> 로 떨어졌다). 자리표시(같은 필터 안에서 경로 검색·정렬을 바꿔 응답을 기다리는 동안 남긴 이전 결과) 동안 판정 창
  문단·지표 정렬 각주는 건수처럼 말하지 않고(이전 결과의 「N개 맞음」을 지금 입력의 범위 이름으로 말했다), 빈 상태의 범위 이름·보조 줄은
  그 결과를 낸 검색(봉투 메아리의 path)으로 말한다.
- **테스트**: `tests/test_usage_attribution.py`(신규 — 덮는 루트 = `pv_root_covering`(무작위 300×20·이웃 이름·비정규 target), 겹친
  루트·루트 충돌의 정체 단위 AND, 인벤토리 > 기록·변조 블록 무시, 모름 셋·밖, 사용자 합집합, 구분 × 축, facets 한 번 세기·상한),
  `test_direct_dest_owner`(pv_roots == sorted(pv_root_index) 등가·읽기 전용·메모 공유 재계산 0회), `test_repo_data_jobs`(요청자 대상 단위·
  고아·matched_count 는 limit 무관·기본 인자면 머리 문장 글자 그대로·기록 블록 최신순·자리표시자 ≤ 500·요청자별 대상 수),
  `test_migrations`(인덱스 집합 무변경, 요청자·기록 질의의 sqlite 계획이 `idx_data_jobs_scan_target`), `test_api_usage_analysis`
  (테스트베드 모양 세계 — nfs 두 클러스터·gpfs models 두 namespace·onefs 요청자 둘·직접 경로·지운 PVC 기록: 봉투·메아리·필터별 결과·창 3
  에서 SQL 순서 보존과 matched null·지표 정렬 값 = 보이는 값·422·내보내기 스트리밍/첫 바이트 전 truncated/도중 실패·facets·필터 합계·
  실제 상수 widest 500; 리뷰 뒤 「판정 창은 정렬과 무관하게 최근 스캔 순 — 네 정렬에서 같은 집합, 순서 = SQL 순서, 내보내기도 같은 창」과
  `_window_order` == SQL 정렬(같은 시각·같은 스캔 횟수 동률 + 콜레이션이 갈리는 이름 — PG 갈래에서 sqlite 와 다른 이름 순으로 확인);
  2차 리뷰 뒤 새 엔드포인트 넷의 storage·path NUL 422(빈 이름·다른 제어문자는 200)와 「facets 의 모든 값이 자기 필터를 통과하고 옵션 수 =
  그 옵션을 고른 목록의 matched_count」(253자 바인딩 이름·259자 `node:` 요청자 포함, 네 스토리지)). 2차 리뷰의 독립 오라클(무작위 세계
  150개 — sqlite 120·PG 30, /targets 1만 500회·내보내기 2,700회·합계 3,600회·facets 4,386회, 창 상한·목록 상한 몽키패치, 변조 블록 8종)은
  불일치 0건이었다(커밋하지 않은 리뷰 하네스).
  포탈 vitest: `UsageAnalysis.test`(+15 — 7개 select·라벨·순서·수, filesystem 탭은 요청자만, 딥링크·필터 합계 URL, 위계, 탭 전환·행
  토글·잘못된 storage 정리의 URL 보존, 「(이 스토리지에 없음)」·0건 툴바, 건수·각주·창 문단(요청자·경로 검색 범위, 정렬 무관), 봉투 아님·
  메아리 불일치·CSV 막힘, 자리표시 행, 6열 + 둘째 줄, 상세 머리, 합계 URL, CSV, facets 실패, 포커스 복귀; 2차 리뷰 뒤 +2 — 기록 0 탭에서
  지운 뒤 포커스가 패널에 남음(빈 상태·필터 줄 두 버튼), 자리표시 동안 창 문단·지표 정렬 각주 없음·빈 상태는 메아리 범위), `UsageFilters.test`(신규 —
  보조 라벨 규칙·판정 창 줄), `usageCsv.test`(+2 — k8s 열·`-filtered`). e2e(수기 게이트): `15-usage-filters`(신규 — 실 서버의 요청자
  축: 선택 → URL → 새로고침 유지 → CSV `-filtered`·k8s 열 → 375 → 지우기), `03-layout`(필터 7개를 모두 켠 딥링크의 375 가로 넘침).
- **세션 가정(사용자 확인 권장)**: 「사용자별」을 권한 사용자(RoleBinding)와 요청자 두 축으로 나눴다 · 필터는 탭을 바꿔도 남는다 · 필터
  합계는 경로 검색을 반영하지 않는다.
- **측정**(리뷰 단계, 1만 대상·6.5만 잡 + PV 600개, PG·sqlite — 수치는 `routes_usage._WINDOW_LIMIT` 주석): 옛 엔드포인트 대비 무필터 목록
  +0.01~0.03초·+0~3MiB, 내보내기 +0.1~0.4초·+4MiB(행의 pv 칸), PV 필터 목록·facets·필터 합계 모두 1.5초 이내, 최악은 동시 3건 내보내기
  +120MiB(옛 export 와 같은 급, api 1Gi 대비 여유). PG generic plan 에서 요청자 경로도 `idx_data_jobs_scan_target` + `idx_requests_requester`.
- **2차 리뷰 오라클**: 스펙 의미를 독립 구현한 무차별 대입 오라클로 무작위 세계 150개(sqlite 120·PG 30 — 클러스터·namespace·PVC 이름
  겹침, 겹친·충돌 루트, 지운 PVC 기록, 변조 블록 8종, 직접 경로·PV 상위, 창 몽키패치)에서 /targets 1.05만·export 2.7천·합계 3.6천·
  facets 4.4천 호출 대조 불일치 0(결함 4종을 심으면 모두 검출).
- **라이브 실증(d12, 소스 efa5c13)**: 실 Chrome — k8s-pv-nfs 필터 줄(클러스터 dmsk8s 2·dmsk8s2 1, namespace·PV 이름·SC·구분 PV 안 3·
  밖 2, 권한 사용자·요청자 — 옵션마다 대상 수), 표 둘째 줄 PV 소속. 딥링크 namespace team-a(dmsk8s) → `team-a/datasets/proof-scan`·
  `dms-proof` + 필터 합계 981.6 KiB, 클러스터 dmsk8s2 → `c2/lab-y/weights`, 구분 PV 밖 → `proof-nl/a/selfnl`·`proof-d9/alice/latest`,
  권한 사용자 cocoa.song → `c2/lab-y/weights`(RoleBinding: lab-y = cocoa.song·junwoo.song), onefs 요청자 alice → `…/dms-proof`(alice·
  junwoo.song 둘 다 scan — 경로 단위). namespace 콤보박스에서 lab-y 고름 → URL `cluster=dmsk8s2&namespace=lab-y`·표 걸림. 펼친 상세의
  PV 소속·권한 사용자(alice, junwoo.song — DB 바인딩과 같음, 중복 바인딩 한 번)·요청자 필터 시 「이력은 요청자와 무관한 이 경로의 전체
  이력」, 상세를 열어도 URL 에 필터 유지. cephfs-dms(filesystem)는 필터 줄 숨김. 390 가로 넘침 0, 콘솔 오류 0. 검증: 전체 백엔드 4326
  passed, 사용량 관련 11파일 sqlite 429·PG 429(+기존 sqlite 전용 7), vitest 1708, tsc(앱·e2e), e2e 30.

### ✅ 경로 봉쇄 개행 구멍(§8 ②) 셸 수정 — **완료**(2026-10-28)

인계 문서 §8(dms-ssc d194 c438667 후속)의 보안 항목을 dms-k8s 에 이식. 경로 봉쇄 셸 함수
(`execution_manifests._PATH_CONTAINED_FN`)가 `$(realpath …)`·`$(dirname …)` **명령 치환이 결과 끝 개행을
지우는** 탓에 두 함정을 가졌다. ① 경로 문자열에 든 개행은 이미 d191 의 case(`*"$dms_nl"*`)가 막았지만,
② **심링크의 타깃 이름이 개행으로 끝나는** 경우(경로 문자열은 깨끗 — 예: 깨끗한 이름 `selfnl` → 이웃 실제
`selfnl⏎`)는 `$(realpath)` 가 타깃 끝 개행을 지워 글자 그대로의 want 와 같아 보여 **봉쇄를 통과**했다
(fail-open). 모든 잡이 root 라 커널 DAC 가 없어, 뚫리면 root 도구가 그 심링크를 따라간다. Python 미러
(`dms_job_runner.contain`, `os.path.realpath` 는 개행 보존)는 처음부터 막았으나 **셸 preflight·exec_preflight 는
열려 있었다** — dash 로 재현했다(핸드오프 §8 이 origin/dms-k8s 에서 재현한 그 구멍).

- **수정**(dms-ssc c438667 이디엄): realpath 는 끝에 「/」 표식을 붙여 받고(`$(realpath -e -- "$x" && echo /)`)
  realpath 자신의 줄바꿈 하나와 표식만 뗀다(`${v%?/}`) — 이름 끝의 개행은 남아 want 와 다르게 보여 막힌다.
  상위 오르기는 `$(dirname …)` 가 아니라 매개변수 확장 `${dms_q%/*}`, 목적지 부모 `dest_parent=${D%/*}`,
  rm 부모 `parent=${1%/*}`(명령 치환을 아예 두지 않는다). k8s 의 ① case·PV 글자 경계(`dms_within`)·rm 끝 링크
  허용은 그대로. 제출은 이미 제어 문자를 `unsafe_path` 로 거부(d191 `domain.validate_relative_path`). Python
  미러는 주석만 갱신(코드는 이미 올바름).
- **검증**: dash 재현 — 수정 전 `selfnl→selfnl⏎` 의 링크 자신·그 아래 새 경로가 셸 IN(fail-open)·Python 차단,
  수정 후 셸·Python 모두 차단. 세 봉쇄 대조 테스트(`test_{sync,rm,pv}_containment_sh.py`)에 ② 사례 추가 →
  셸·러너 같은 판정. 보안 리뷰 3관점(우회 탐색·셸 이식성·셸↔Python 일치) — 개행 1~3개 타깃·링크 체인·
  중간 성분·개행-실명 루트 링크·PV·rm 끝 링크×② 등 적대적 프로브 전부 fail-open 0건. busybox-ash 는
  번들 realpath 가 `-e`/`--` 미지원이라 전수 fail-closed(보안 구멍 아님·배포 셸은 dash). 백엔드 봉쇄·계약
  배치 580 passed, 전체 백엔드 4245 passed.
- **라이브 실증(d11, 소스 cb0d9b9)**: NFS 에 깨끗한 이름 `selfnl` → 이웃 실제 `selfnl⏎` 를 두고 제출 —
  ② sync 목적지(`selfnl/new`)·소스(`selfnl`) 모두 Rejected `destination/source_path_redirected`, ② rm
  상위(`selfnl/old`) Rejected `target_path_redirected`(preflight 가 막는다 — 수정 전엔 통과했다). 과차단 없음:
  정상 `a/normal/new` 는 ConfirmPending 진행. rm 끝 링크(`selfnl` 자신, ②)는 ConfirmPending 허용(drm 이 링크만
  삭제 — 설계). 직접 경로 scan `selfnl` 은 Succeeded(직접 경로 scan 은 봉쇄 밖 — 읽기 전용·특권 운영자 전용,
  개행 구멍과 무관). 경로 문자열 개행(①) 제출은 422 `unsafe_path`. 실증 데이터·대기 요청 정리.

### ✅ k8s 연동 호출 노드 카드 — 해결된 실패 구분·클러스터별 연결·보조 연결 확인 — **완료**(2026-10-28)

사용자 보고(「K8s 연동 메뉴 알림」): 개요(/admin/k8s)의 호출 노드 카드가 이미 해결된 실패를 지금 오류처럼 보였다. 라이브 분석 —
k8s-w1(primary)은 마지막 호출·성공이 2분 전인데 「마지막 실패 1일 전 · 호출 노드에서 API 서버에 연결하지 못했습니다 — 방화벽…」이
빨간색이었다(실제: 2026-10-26T03:26:51Z dmsk8s2 수동 동기화 1건이 실험 클러스터 컨테이너 재시작 2분 동안 Connection refused, 03:29:06 부터
쭉 성공). k8s-w2(secondary)는 그 직후 대체 재시도 1건도 같은 이유로 실패했고, secondary 는 대체 때만 불려서 그 뒤 한 번도 안 불려
`callerNodeState` 가 「최근 실패」(bad)에 45시간째 고착됐다. 정작 중요한 사실 — w2 는 10-26 에 등록한 dmsk8s2 에 **한 번도 성공한 적이
없다**(장애 넘김 경로 미검증) — 은 화면 어디에도 없었다.

- **보조 연결 확인(trigger `check`)**: `k8s_clusters.check_requested_at`(CREATE + `_ensure_columns` 이중 경로). `k8s_sync.claim_trigger`
  배정 규칙 (3) — secondary 전용(주·보조가 둘 다 지정됐을 때만), 대체 다음·due 앞, primary 는 절대 받지 않는다. claim 은 대체와 같은
  우선(0)으로 각자의 시각(failover_after·check_requested_at) 순 정렬하고, 이긴 claim 의 trigger 가 check 일 때만 요청을 지운다(primary 의
  정규 claim·secondary 의 대체 claim 이 확인 요청을 삼키지 않게). 확인 claim 은 `check_requested_at` 만 지운다 — last_attempt_at·
  sync_requested_at·failover_after 는 그대로(정규 일정을 밀거나 걸린 「지금 동기화」·대체 예약을 삼키지 않게). 두 CAS 는 서로 상대가
  바꾸는 칸을 둘 다 WHERE 에 넣고, 후보 행을 활성 run 판정보다 먼저 읽는다(같은 클러스터에 run 이 둘 생기는 경합 차단). 확인 run 의
  노드성 실패(연결·DNS·시간 초과·기한 초과 — `FAILOVER_REASONS`)는 클러스터 실패 요약(연속 실패·사유·오류)과 대체 예약을 건드리지
  않는다(`k8s_sync.touches_cluster_summary` — `_record_failure_in_txn` 한 곳이 receive·expire_overdue·fail_apply 세 길을 거른다; 실패
  전이 이벤트도 없다). tls·401·403·적용 실패처럼 어느 노드에서나 같은 실패는 확인 run 이라도 정규처럼 기록한다. 성공은 정규와 같다.
  라우트 `POST /api/admin/k8s/secondary:check`(require_admin — `:sync` 와 같은 등급) → 활성이고 토큰을 열 수 있는 클러스터 전부 요청,
  409 `k8s_sync_no_secondary`·`k8s_sync_no_primary`·`k8s_sync_secondary_not_ready`(보조 노드 보고 지연·없음·미지원 — 가져갈 노드가
  없는 요청이 무기한 남지 않게)·`k8s_sync_nothing_to_check`(reasonCodes.json·REASON_MESSAGES 양쪽). 주·보조 중 하나를 비우거나 보조
  노드를 바꾸면 남은 요청을 지운다(`update_settings` — 요청은 그때의 보조 노드가 가져갈 수 있는지 보고 받았다; 미지원 노드로 바꾸면
  요청이 영영 남았다).
- **노드 통계 확장**(`node_run_stats` — (노드, 클러스터, 지금 설정) GROUP BY 한 번 + 최근 run·최근 실패 run 행 한 번): 노드의
  `last_fail_cluster`·`last_fail_trigger`·`last_fail_cluster_ok_at`(마지막 실패의 클러스터에 대한 이 노드의 가장 최근 성공 — 설정
  세대 무관, 「그 실패가 그 뒤 해결됐나」의 값), 노드 × 지금 있는 클러스터마다 `{last_ok_at, last_fail_at, last_reason, last_trigger,
  last_status}`(ok·failed·running·null = 호출 기록 없음 — 부른 적 없거나 보존 정리가 지웠다, 모름이지 실패가 아니다; superseded·
  discarded 는 판정에 쓰지 않는다; 적용 실패는 노드가 닿았으니 ok). 클러스터별 칸은 **지금 설정**의 run 만 센다 — 그 클러스터
  created_at 이후이고 run.config_rev = 클러스터 config_rev(같은 이름으로 다시 등록하거나 PUT 으로 주소·TLS·토큰을 바꾼 클러스터가 옛
  설정의 성공을 물려받아 새 주소에 닿은 적 없는 secondary 가 「성공」으로 보이지 않게; 설정이 바뀌는 사이 버려진 확인 run 도 옛
  성공으로 돌아가지 않고 「호출 기록 없음」). secondary 의 `check_pending`. 클러스터 요약에 `check_requested_at`.
  클러스터 카드의 마지막 실행·「맡은 클러스터」는 확인 run 을 빼고 고른다(`latest_run_per_cluster` — 확인 한 번이 주·보조 표시를
  뒤집지 않게).
- **포탈**: 「마지막 실패」는 그 뒤 성공했으면(**그 클러스터**에 대한 이 노드의 성공 `last_fail_cluster_ok_at` 으로 잰다 — 다른
  클러스터의 성공으로 「해결됨」이라 하지 않게; 배지도 같은 판정 `k8sTerms.failureResolved` 하나를 쓴다) 흐린 글씨로 「언제 · 클러스터 계기 · 짧은 사유 · 이후 성공해 해결됨」(조치 문구 없음 —
  `k8sTerms.SYNC_REASON_SHORT`·`shortReasonText`), 해결 안 된 실패는 빨간 전체 사유 + 클러스터 이름. 배지는 secondary 이고 실패한
  클러스터가 이후 다른 노드로 회복했고 그 실패가 확인 run 자체가 아니면 「재확인 필요」(action — `pathUnverifiedSince`; primary 는 매
  주기 스스로 다시 부르므로 그 실패는 늘 「최근 실패」). 「클러스터별 연결」 줄(성공·실패·진행 중·호출 기록 없음 — secondary 면 「장애
  넘김 경로 미확인」; 아이콘 + 글자; 「클러스터는 이후 회복」 꼬리표는 보조 연결 확인의 실패엔 붙이지 않는다 — 클러스터는 실패한 적이
  없다). secondary 슬롯의 「연결 확인」 버튼(`useCheckSecondary`): 요청이 남은 동안 잠그고 개요를 5초로 다시
  읽는다(`overviewPollMs` — 보고가 끊기거나 k8s 동기화를 지원하지 않는 노드의 요청은 빠르게 읽지 않고 못 가져가는 이유를 안내),
  보고 없음·지연·미지원·
  primary 미지정이면 잠그고 이유를 적는다. 맡은 클러스터 꼬리표는 긴 이름도 줄을 바꾼다(390px 가로 넘침 없음 — 빌드한 포탈을 가짜 API 로
  실 Chrome 에서 확인). 동기화 이력의 계기 칸은 `TRIGGER_LABEL` 하나로 「보조 연결 확인」을 읽는다.
- **라이브 실증(d10, 소스 cd53224)**: 배포 직후 개요 — w1 「마지막 실패 2일 전 · dmsk8s2 수동 · API 서버 연결 실패 · 이후 성공해
  해결됨」(회색), 클러스터별 연결 둘 다 성공; w2 배지 「재확인 필요」 + 「dmsk8s2 는 이후 다른 노드로 회복 — 이 노드의 경로는 그 뒤
  확인되지 않았습니다」 + dmsk8s2 실패 줄. 실 Chrome 에서 「연결 확인」 → 버튼 잠김·「확인 요청됨 … 남은 클러스터 dmsk8s, dmsk8s2」 →
  7초 뒤 w2 의 check run 둘 다 applied(w2 가 dmsk8s2 에 처음 닿음 — 장애 넘김 경로 실검증) → w2 「대기」·클러스터별 연결 둘 다
  「성공 · 방금」·마지막 실패 회색 해결됨. 맡은 클러스터(w1)·클러스터 카드 마지막 실행(w1 정규)·연속 실패 0 은 그대로, 동기화 이력에
  「k8s-w2 보조 · 보조 연결 확인 · 적용됨」 2행. 1440·390px 가로 넘침 0, 콘솔 오류 0. 검증: 백엔드 전체 4234 passed + k8s 관련
  343·PG 137, vitest 1686, tsc, e2e 29. 원인 확인: 그 실패는 `dms-k3s2` 컨테이너(dmsk8s2)가 10-26 03:28:52Z 에 다시 시작되던
  2분 사이였다(`docker inspect` StartedAt, RestartCount 0 — 수동 재시작).

### ✅ dms-ssc 이식 — mpifileutils 7a3189c · d190 · d191(경로 봉쇄 통일) · d192 · d193 — **완료·실증**(2026-10-28, d9)

DMS-SSC 세션 인계(`origin/dms-ssc:docs/history/handoff-2026-10-28-dms-k8s.md` — 0절 순서 핀 → d190 → d191 → d192 → d193)를 dms-k8s
(k8s 신원 모드·모든 잡 root·PV/PVC 주소)에 맞게 옮겼다. 사용자 결정(설계 질문 둘, 원문): 경로 봉쇄는 「한 규칙으로 통일」, rm 끝 링크는
「허용 유지」. 포탈은 UI 개선도 함께(「포탈 UI 도 개선」). ssc 커밋(c4a2562 핀·d7ec11e d190·4a13415 d191·4a27b6e d192·d83446f d193)은
기준판으로만 읽고 작업 트리에서 손으로 맞췄다 — LDAP 전용 조각(`groupDeleteWarning`·비 root 폴백·보조 그룹·`identity_groups_not_applied`·
`identity_changed_at_step`·`artifact_base_group_writable`·`ldap_*`·「실행 신원 소유가 아닌 파일」 문구)은 들이지 않았다. 지금 동작의 지도는
ARCHITECTURE(§4 「미리보기·실행 실패 사유」, §5 metrics 행·`files_count` 불릿, §6 「경로 봉쇄」·「PV 주소 봉쇄」·「summary.json」·옵션 기본값의
direct + bufsize, §7 `toolSummary`·배치 확인 `files_by_tool`), 운영은 deploy/README §12d.

- **mpifileutils 핀 ae781bd → 7a3189c**(`Dockerfile.mpifileutils` — 공용 파일이라 ssc 판 이력 주석 + k8s 주석 한 벌; 포크 브랜치
  `nsync-fixes-d189` = 2026-10-28 포크 main): 주 이유는 **C8**(nsync 가 디렉터리 → 파일 교체에서 `--delete` 없이 목적지 전용 항목을 재귀
  삭제 — 모든 잡이 root 라 그대로 데이터 손실, c4dbf20)과 nsync `--open-noatime` 무동작(빌드에 `_GNU_SOURCE` 가 없어 포탈이 늘 싣는데도
  root 실행이 소스 atime 을 보존하지 않았다, db1e33c). 함께 들어온 것: 배치 ≥2·목적지 rank ≥2 타입 교체(C4·C7·C10)·`--batch-files 0`(C6)·
  목적지 루트 심링크(C12)·dsync 디렉터리 0644(D3) 수정, nsync 항목별 실패 목록·`--max-errors`(기본 65,000 — DMS 는 넘기지 않는다)·
  ENOSPC/EDQUOT/EROFS 즉시 정지·`Completed actions:`, drm·dsync `--delete` 부분 실패 rc≠0 + 「Removed N」 = 지운 수 + `Remove failed for F
  of T items`, dsync 목적지 부모 쓰기 불가 rc 1·소스 특수 파일 경고 한 줄. dscan 은 ae781bd 그대로(총바이트·이름 카운터 유지).
  `test_k8s_submit` 고정값 테스트(거부 목록)는 그대로 통과.
- **d190 옵션 문구**: `optionRules.OPEN_NOATIME_HELP`(k8s 의 「기본 켬 … 모든 작업이 root」 + relatime 은 하루 한 번 정도 갱신·noatime·
  일부 CephFS 는 갱신 안 됨), `DIRECT_HELP`(공존 노드 문장 유지 + nsync 는 받기만 함 + bufsize 조건). 옛 비 root 행 전용
  `requestSpec.noatimeWarn`·`RequestSpecCard.NOATIME_DETAIL` 삭제(7a3189c 부터는 비 root 여도 EPERM 뒤 O_NOATIME 없이 다시 연다).
  요청 상세 옵션 칩은 nsync 로 돈 요청의 direct 에 「적용되지 않음」(`requestSpec.TOOL_IGNORED`). ssc 의 「비 root 관리자 실행에도
  open_noatime」은 k8s 에 이미 같은 효과(전부 root)라 해당 없고, 배치 전용 문구도 만들지 않았다(단건·배치 모두 root).
- **d191 경로 봉쇄 — 한 규칙으로 통일**(설계 메모의 표 그대로): 셸 `execution_manifests._PATH_CONTAINED_FN`(`dms_path_contained PATH
  ROOT` — ssc d191 그대로 + 빈 ROOT 거부 한 줄)과 Python 미러 `dms_job_runner.contain.path_redirected` 하나가 sync 소스·목적지(**직접
  경로 포함** — 전엔 봉쇄 밖이라 root sync 가 링크 너머 남의 디렉터리에 쓰고 소유를 바꾸거나(C12) 소스 링크 + delete 로 목적지를 지울 수
  있었다(C13))·rm 대상의 상위·PV scan 대상을 본다. 아직 없는 끝은 존재하는 가장 깊은 상위로(예전 PV 규칙은 부모 하나만), PV 쪽은 여기에
  PV 루트 글자 경계(`dms_within`/`contain.within`, 합친 판정 `contain.entry_redirected`)만 더한다. 접은 것: `_PV_CONTAIN_FN`(`dms_contain`)·
  `contain.pv_path_redirected`·`paths.contain[*].anchor`. 기준 root 는 stepper 가 경로를 조합한 같은 값을 싣는다(sync
  `paths["source_root"/"destination_root"]` — 없으면 `_sync_roots` ValueError → submit_failed). 재확인 지점은 k8s 방식을 직접 경로까지
  넓혔다: preflight·exec_preflight(sync·PV scan 은 각 쪽 첫 검사 — PV scan 은 예전 `test -r` 뒤라 끊긴 링크가 `target_not_readable` 이었다),
  dsync·dscan 런처 mpirun 직전(6d, env `DMS_JR_CONTAIN` — sync 는 늘, 직접 경로 쪽은 경계 = 기준), drm 런처(6c), nsync 각 rank 의 도구 exec
  직전 + 런처 끌어올리기(7b). 사유: 신설 `source_path_redirected`·`destination_path_redirected`(sync — PV·직접 공통), `target_path_redirected`
  는 rm·PV scan(문구 넓게), 진단 줄 `DMS_JR_PATH_REDIRECTED side=… path=… root=… bound=…`. 배포를 건너 옛 잡 이미지가 찍는 sync 마커
  (`target_path_redirected` + `DMS_JR_PV_PATH_REDIRECTED side=…`)는 `parse_execution_reason(operation="sync")` 가 쪽별 코드로 다시 가르고,
  옛 러너도 같은 4칸 env 를 옛 PV 규칙으로 읽어 배포 순서와 무관하게 열리지 않는다. **rm 끝 링크 허용 유지**(사용자 결정 — drm 은 링크만
  지운다, d183 실증): 대상의 **상위**에만 규칙을 걸고 managed_root 자신·끝 `/` `.` `..` 는 막는다. **직접 경로 scan 은 봉쇄 없음**(특권
  운영자 전용·읽기만 — 매니페스트 바이트 동일).
- **d192 러너 — summary v2 + 도구 결과 사유**(잡 이미지): `parsers.parse_tool_detail`(ssc 와 같다 — k8s 의 dscan `total_bytes`·`_is_count`
  유지), `runner._build_summary`·`_derive_reason`·`_type_change_only`·`_tool_failure_report`·`_echo_tail`(ssc 와 바이트 동일). 핵심 3키는
  그대로, 상세는 `detail_version: 2` 아래; 실행 files = 실제로 일어난 일(nsync `Completed actions` 합·dsync 최종 Items − 실패 복사·drm 지운 수).
  rc≠0 이면 launcher 로그에 `DMS_EXEC_REASON=<code>` 를 먼저 찍고 도구 출력 발췌(`DMS_JR_TOOL_OUTPUT` 머리 + stdout 80·stderr 20줄, 합계
  10 KiB, 줄마다 `  | `)를 붙인다. `run_job` 순서 = 6c/6d 봉쇄 → mpirun → stdout/stderr → **7b(nsync rank 마커 끌어올리기) → 8 summary →
  9 사유·발췌** — 7b 가 뒤로 가면 도구 사유가 봉쇄 마커보다 먼저 찍혀 첫 마커를 빼앗는다(주석으로 고정). `TARGET_PATH_REDIRECTED` 는
  러너에서 재정의하지 않고 contain 에서 import. 3키만 쓰는 경로는 미지 도구·워커 준비 실패·봉쇄 거부(returncode null), quiet 는 머리 키 +
  `quiet: true` + 수 null(ssc 와 같다).
- **d192 제어면**: `execution_manifests.TOOL_FAILURE_REASONS`(9) — `EXECUTION_REASONS` = 워커·봉쇄 넷 | `TOOL_FAILURE_REASONS`(ssc 의
  `identity_groups_not_applied` 없음), 승격은 기존 경로(`stepper._run_failure_reason` → `parse_execution_reason`), 발췌 줄은 `| ` 로 시작해
  마커로도 옛 이미지용 쪽 매핑(`_LEGACY_REDIRECT_SIDES`)으로도 읽히지 않는다. `stepper._surface_failed_artifact(promote_counts=)` +
  `data_jobs.set_artifact(promote_counts=)` — 실행 실패만 files/bytes 승격(미리보기 실패·시간초과는 표시만). `GET /api/admin/metrics/jobs`:
  `files_total`·`bytes_total` → `sync_items_total`·`sync_bytes_total`·`rm_items_total`(모든 종단 + `exec_submitted_at`, 성공이 아닌 행은 v2 만 —
  LIKE 패턴은 k8s 의 `text_search.contains_pattern`), 분해 행 succeeded / failed(+TimedOut) / rejected / cancelled(+PreviewExpired), failure_reasons
  는 Failed·TimedOut·Rejected 만. 옵션: `domain.direct_bufsize_ok` + 422 `direct_bufsize_misaligned`(direct 면 bufsize 4096·2^k ≤ 4 MiB) — 단건
  (직접 경로·PV 주소)·배치 생성·항목 추가/수정/교체·실행 설정·다시 돌리기(`_reject_stale_options`)·자식 생성·자동 확인 보류가 모두
  `validate_options`(배치 CSV 해석은 옵션을 받지 않는다). 배치: `list_items_detail` 의 `job_tool` 조인, 확인 감사 행 `files_by_tool`(잡 행
  도구가 권위).
- **d192·d193 포탈**: 새 `lib/toolSummary.ts`(readSummary·summaryTool·failureCounts·previewWarnings·opLabel·errnoText·PRE_TOOL_REASONS).
  StageResults(도구별 타일·「실패 항목」 연산별 칩·표본·errno·d193 dsync 0 B 잔여 안내·옛 3키는 중립 라벨), ConfirmDialog(도구별 요약 + 주의
  상자 — d193 「교체를 시도합니다」; k8s 「결과 소유」 줄 유지, 순서 요약 → 주의 → 결과 소유), requestOutcome·OutcomeCard(도구 시작 전 거부 =
  「데이터는 바뀌지 않았습니다」·반영 수·KPI 5칸·「확인할 점 N가지」, k8s exactBytes title 유지), BatchDetail(도구 뜻별 미리보기 합계 — nsync 변경
  예정·dsync 소스 전체, 타입 교체·특수 파일·direct 의 nsync 안내, 「지정 경로」), BatchExecutionSettingsDialog·SubmitJob·BatchCreate(direct·quiet
  를 FieldRow 설명으로, bufsize 즉답 `directBufsizeError` — 서버 422 와 같은 문장, `SYNC_MAX_ERRORS_NOTE`·`BUFSIZE_HELP`·`QUIET_HELP`, 링크 규칙
  줄), Dashboard·JobStatsSection(연산별 처리량 타일·분해 표 성공/실패/거부/취소·만료·「실패(24h)」 = Failed+TimedOut·d193 lg 2/2xl 3 열), usage
  (atime 규칙 문구), `format.bytesText`. 사유 코드 12개(reasonCodes.json·REASON_MESSAGES 양쪽): d191 `source_path_redirected`·
  `destination_path_redirected`, d192 `tool_aborted`·`preview_scan_incomplete`·`destination_full`·`destination_read_only`·`sync_stopped_max_errors`·
  `sync_type_change_refused`·`source_scan_incomplete`·`sync_items_failed`·`rm_items_failed`·`direct_bufsize_misaligned`; `target_path_redirected` 문구는
  rm·PV scan 공용으로 넓혔다.
- **ssc 와 다르게 한 것**(이유):
  1. rm 끝 링크 허용(사용자 결정 — ssc d191 은 거부한다). 포탈 `storagePaths.RM_PATH_LINK_RULE` 도 「중간 단계(관리 디렉토리·PV 아래)가 링크면
     거부, 대상 자신이 링크면 링크만 지우고 가리키는 곳은 그대로」(ssc 의 「링크 자체도 지우지 않음」을 쓰면 틀린다).
  2. 워커 셸(sshd 앞) 재확인은 들이지 않았다 — dsync 런처 6d·nsync rank exec 직전이 더 늦은 자리다(`_worker_command_script` 무변경, 테스트가 고정).
  3. PV sync 사유도 쪽별(ssc 엔 PV 주소가 없다) + 옛 잡 이미지 마커의 쪽 매핑. 꼬리 안의 옛 진단 줄은 매핑에 걸리지 않는다(k8s 고유 테스트).
  4. `PRE_TOOL_REASONS` 는 k8s 집합 — stepper 제출 전 fail-closed·PENDING 재검사·`k8s_access.recheck_request`·`_record_pv_owner`·러너 봉쇄와 대조
     (`chown_not_allowed`·`pv_owner_*`·`storage_owner_changed`·`pv_moved`·`k8s_access_denied`·`privileged_not_authorized` 등 포함, LDAP 사유 제외,
     `path_conflict` 은 요청 단위 Conflict 라 제외). `requestOutcome.QUEUE_GATE_REASONS` = node_excluded_at_step·node_cpu_insufficient_at_step
     (k8s 에는 `_note_queued_recheck` 가 없어 `identity_missing_at_step` 은 큐 관문이 아니다).
  5. 문구: `preview_scan_incomplete`·`SYNC_OPTION_HELP.contents` 는 「실행 계정 권한」 대신 스토리지 쪽(NFS root_squash·마운트 상태), set-ID 제거
     노트(`syncOwnership.SETID_STRIP_NOTE`)는 명시 chown·옛 비 root 자동 지정에 붙이고 PV·스토리지 기본 소유자 규칙엔 「디렉토리 setgid 유지」만
     더했다(그 규칙 문구가 이미 setuid 제거를 말한다).
  6. 「실제 경로」 → 「지정 경로」를 k8s PV UI 까지(LocationField·PvPathPicker·SubmitJob 요약·pvLocation 주석 — PV 루트 + 하위경로를 글자 그대로
     붙인 것이라서). VolumesPage 의 인벤토리 「실제 경로」는 그대로.
  7. 결과 파일 만료(k8s 전용 `artifact_expired`): 만료면 「stdout.log 열기」 등 결과 파일 버튼을 만들지 않고 만료 문장만(`JobStages.openOutput`
     null·StageResults `outputExpired`·requestOutcome 실패 배너) — ssc 판 그대로면 410 으로 끝나는 막다른 버튼이 된다.
  8. scan 원 키 타일: dscan `total_bytes`(k8s 포크 카운터)를 날 수 대신 「1.0 GiB (1,074,750,158 B)」로(`StageResults.RAW_BYTE_KEYS`),
     `invalid_name_entries` 는 수 그대로(0 은 0).
  9. `tests/test_direct_bufsize_paths.py` 는 k8s 용으로 새로 썼다 — ssc 판의 공유 토큰 + `run_as_root: False` 는 k8s 에서 403 이라
     `test_k8s_submit` 의 world/kclient 픽스처로 직접 경로는 ops 세션 + root, PV 주소는 alice(`_pv_sync`), 항목 추가·수정·교체와
     `direct_bufsize_ok` 단위 표를 더했다.
- **테스트**: 새 `test_sync_containment_sh`(셸·Python 판정을 같은 트리·실 dash 로 대조)·`test_job_runner_tool_detail`(실 출력 15종 픽스처 +
  러너·제어면 사유 상수 계약)·`test_direct_bufsize_paths`, 보강 `test_rm_containment_sh`·`test_pv_containment_sh`(「키 없는 nsync」 검사를 줄
  단위로 — 발췌 줄 `  | DMS_EXEC_REASON=…` 은 마커가 아니다)·`test_stepper_execution_reason`(ssc 셋 + k8s 하나)·`test_execution_manifests`
  (`test_worker_script_emits_no_exec_reason_marker` 기대 집합에 `TOOL_FAILURE_REASONS`)·`test_direct_dest_owner`·`test_pv_owner_chown`·
  `test_api_metrics`·`test_repo_metrics`·`test_api_batches`·`test_repo_batches`·`test_repo_data_jobs`·`test_domain_options` 등, 포탈 `toolSummary.test`
  새 파일 + 각 화면 테스트(옛 행에 supplementary 키가 남아도 그룹 경고가 생기지 않음, ConfirmDialog 순서, 만료 처리), e2e `03-layout`
  (「지정 경로: 」)·`11-batch-rm`(「지정 경로:」, 「실제 경로」 없음)·`fixtures/e2e_controller.py`(summary v2 + 사유 마커 — 가짜 drm 출력을 실
  러너로 돌려 files 4 / target_items 4 / removed 4 확인).
- 검증: 백엔드 전체 4,179 통과·12 건너뜀(4조각 병렬, 실패 1건 — 위 `test_worker_script_emits_no_exec_reason_marker` 기대 집합 — 고친 뒤
  재실행 통과), vitest 105 파일 1,672 통과·`tsc -b` 통과. **아직**: e2e(수기 게이트)·테스트베드 적용·라이브 실증. 모든 잡이 root 라 권한 실패
  모양(ssc N1·N4·R1·D2)은 재현되지 않는다 — 포크 하네스와 러너 실출력 테스트로 갈음하고, 라이브는 N2·D1(타입 교체 거부)·N3(delete + 링크 +
  FIFO)·CephFS 쿼터 `destination_full`·O1(direct + bufsize 422/수락, 단건 PV·직접·배치·실행 설정)·대시보드 합계 대조·7b 순서(컨펌 뒤 Volcano
  대기 중 nsync 목적지를 링크로 교체 → `destination_path_redirected`, summary null 3키)·미리보기 실패의 `files_count` NULL·만료된 잡의 실패 화면,
  그리고 d191 설계 메모의 봉쇄 13케이스(직접 경로 C12·C13·중간 링크·늦은 재확인·PV·rm 끝 링크·직접 경로 scan)를 본다.
- **리뷰(독립 4차원 + 반박 검증) 반영 — 확인 3건**: ① [medium] dsync 수 위조 — 원시 경로 오류 줄(복사한 것 없는 실행의 walk 「Path name is
  too long」, 진짜 블록 뒤 갱신 실패 줄)에 개행 이름으로 심은 `Seconds/Items/Data` 블록이 files·bytes 와 대시보드 합계를 부풀렸다(ssc d192 도 같음
  — 옛 「마지막 매치」 규칙). `parsers._sync_final_block` 이 마지막 walk 요약 뒤·timestamp 머리 줄 앞·배치 진행 블록 제외 구간에서 **정확히 하나**
  일 때만 받고, 미리보기는 walk 수만(`parse_sync_dryrun_counts`). ② [low] 끝이 개행인 경로 성분에서 셸 `$(dirname)`·`$(realpath)` 가 개행을 잘라
  셸·파이썬 봉쇄 판정이 갈렸다(파이썬 재확인이 막아 데이터는 무사) — 개행이 든 경로는 셸·파이썬 모두 fail-closed 거부, 제출에서도 제어 문자
  (\x01-\x1f, \x7f) `unsafe_path`. ③ [low] 러너 7b(rank 표식 끌어올리기)가 사유·꼬리 블록보다 앞이어야 하는 순서를 고정하는 테스트. 세 수정 모두
  수정을 빼면 새 테스트가 실패함을 변이로 확인. 반박: 없음(확인 3건 전부 반영).
- 남는 것(BACKLOG §1 「dms-ssc 이식 후속」·§4): dsync 미리보기 변경 계획 · dsync 실패 파일 0 B 잔여 · dsync·drm 오류 줄 경로 미이스케이프 ·
  dsync walk 오류는 `--delete` 일 때만 · 포크 루트 고정(B) · nsync P3 · 이식 전에 계획된 direct + 맞지 않는 bufsize 잡.

**테스트베드 적용 — d9**(2026-10-28, 소스 110a6b2): 포탈 빌드 d9(이미지 3종 + 잡 이미지 — mpifileutils 7a3189c, 210초) → 릴리스 → guard →
apply -k → migrate → rollout restart, verify PASS 4. 검증: 백엔드 전체 4,180 통과(리뷰 수정 전 스냅숏) + 수정 영향 640 통과, vitest 1,672·tsc·e2e 29,
dist 외부 URL 0. 라이브 실증(전부 기대대로):
- **경로 봉쇄(통일)**: 직접 경로 dsync 목적지 루트 링크(C12) → `destination_path_redirected`·소스 링크 + delete(C13) → `source_path_redirected`(남의
  데이터 불변), 중간 링크 양쪽 거부, 상위 없는 새 목적지는 봉쇄가 아니라 `destination_parent_not_writable`, 미리보기 뒤 목적지를 링크로 바꾸면 실행 직전
  재점검이 거부 — dsync(nfs 직접 경로)·**nsync**(cephfs-third → cephfs-secondary, 노드 둘) 모두, PV sync 소스 링크 `source_path_redirected`(예전엔
  target), PV scan 링크 `target_path_redirected`, **rm 끝 링크는 링크만 삭제**(가리키던 victim 그대로, 사용자 결정)·rm 중간 링크 거부, 직접 경로 scan
  은 봉쇄 밖(결정 — 링크 너머를 스캔).
- **도구 결과(d192)**: nsync 타입 교체(목적지 비어 있지 않은 디렉터리, 삭제 꺼짐) — 미리보기 `type_change_needs_delete` 경고 → 실행
  `sync_type_change_refused`(replace-dir 1)·**목적지 내용 보존**(ae781bd 의 C8 데이터 손실 해소) · dsync 타입 교체 → `sync_type_change_refused`·목적지
  보존 · nsync delete + 링크 + FIFO → Succeeded, 반영 = 계획(복사 2·디렉토리 1·링크 1·속성 1·삭제 1), FIFO 1 건너뜀 · CephFS 쿼터 1 MiB(pkg-01 관리자
  키로 설정)에 6 MiB → `destination_full` 즉시 정지 · direct + bufsize 8 MiB·1000000 → 422 `direct_bufsize_misaligned`, 1 MiB 수락 · nsync
  `--open-noatime` 실효(Options 줄 `open-noatime=1` — db1e33c) · 대시보드 24h `sync_items_total` 11·`sync_bytes_total` 8·`rm_items_total` 1 이 DB 손
  합계(1+6+0+4, dsync 모름 제외)와 일치.
- **실 Chrome**: 도구 전 거부 배너 「데이터는 바뀌지 않았습니다」 + 새 사유 문구, 타입 교체 실패 「실패로 끝나기 전까지 속성 갱신 1개 반영」·「실패 항목」
  안내, 가득 참 배너, 반영 타일·FIFO 안내, 컨펌 창 「변경 예정 2개(복사 1·삭제 1)」 + 교체 시도·비어 있지 않은 디렉토리 경고 + 「결과 소유」, 대시보드
  1440·768 넘침 0, 제출 화면 rm 링크 규칙(끝 링크는 링크만 지움), 콘솔 오류 0. 실증 데이터·쿼터 디렉터리 삭제, 확인 대기 요청 취소.

### ✅ BACKLOG 결정 묶음 구현 — 직접 경로 결과 소유·rm 경로 겹침 잠금·결과 파일 자동 만료·배치 삭제 상한 2000·dscan 카운터 + 보류 기능 5건 + 결함 3건 — **완료·실증**(2026-10-26~27, d6)

사용자 결정(원문): 「A 결정 필요한 것들 1. 권장안대로 구현. 2. 권장안대로 구현하되, 구현 후 다양한 케이스들에 대해 검증을 하여
제대로 작동하는지 꼼꼼히 체크 필요. 3. 권장안대로 파일만 만료하고 행은 남김. N일의 N 기본값 1500일로 하자. 4. 큰 배치 삭제 숫자를
늘려줘: 작업 2000개 또는 항목 10000개 초과. 5. 카운터만 둠 6. Pure flashblade : 백로그에 남겨둠」, 「C 보류된 기능 - 요청 상세의
단건 삭제 버튼 구현 - 배치 기록 삭제의 감사 행 CAS 구현 - 배치 csv 백엔드화 구현 - 배치 생성의 스토리지 드롭다운 구현 - dscan
총바이트 집계 구현」, 「D 고쳐야 할 결함: - 빌드 파드 gc가 200건 창에 고착 - 빌드 삭제 시 파드 누수 - securitycontext 드리프트를
배지가 못븀」, 「위 내용들 구현 및 해결하고 실증및 푸시까지 마무리해.」 A 의 번호는 결정 목록 순서다 — 1 직접 경로 목적지의 결과 소유,
2 rm 경로의 다른 요청·배치와의 겹침, 3 아티팩트 보존 정책, 4 큰 배치 삭제, 5 비 UTF-8 이름, 6 Pure FlashBlade. 현재 동작의 지도는
ARCHITECTURE(각 항목의 절), 운영은 deploy/README §8-2b·§9-8·§12·§12b·§12c.

- **A1 직접 경로 목적지의 결과 소유**(권장안 (4) + 다듬기 「both 는 경로가 인벤토리의 PV 루트 아래일 때만」): 특권 운영자의 직접 경로
  sync(단건·배치 자식)에서 목적지 스토리지가 pv 종류면 늘, both 면 정규화한 경로가 **지금** 인벤토리의 그 스토리지 PV 루트
  (`repositories.k8s.pv_roots`·`pv_root_covering` — 적용된 모든 클러스터, 글자 경계, 입력 지문 + 30초 메모) 아래일 때 그 스토리지
  기본 소유자로 `--chown`(측정 없음, 0:0 도 넣는다 — setuid/setgid 제거). 판정 `k8s_access.direct_dest_owner` 하나를 단건 제출
  (`routes_requests.submit` — 무효 저장값 409 `invalid_storage_owner`)과 배치 자식 생성(`batch_orchestrator._materialize` — 그 항목만
  Rejected)이 payload `dest_owner`(`{"rule":"storage",…}` | `{"rule":"none",…}` — "none" 은 키 없는 옛 행과 구별하려는 기록)로 얼리고,
  `recheck_request`(`_recheck_dest_owner`)가 다시 계산해 (규칙, 소유자)가 다르면 `storage_owner_changed`. 배치 자식은 단건 컨펌을 지나지
  않으므로 `BatchOrchestrator._confirm_child` 가 실행으로 넘기기 직전 `recheck_request` 를 부른다(실패 = 잡 Rejected + 요청 화해 +
  `batch_child_recheck_rejected`). 실행은 `JobSpec.result_owner_direct` + `execution_manifests.direct_dest_owner_applies`. 포탈:
  `lib/syncOwnership` 의 `directDest`(storage / storage_if_under_pv / varies), SubmitJob·BatchCreate·BatchDetail·requestSpec
  「목적지 소유」, `/api/user/storages` 관리자 응답의 `default_owner`. 새 사유 코드·컬럼 없음.
- **A2 rm 경로 겹침 잠금**(권장안 「경로 접두 잠금」, 2026-10-27): 한쪽이 rm(·미지 연산)인 두 작업이 같은 스토리지 이름에서 경로
  성분 단위로 겹치면 늦은 쪽이 기다린다 — 술어 `domain.overlap_path`·`overlap_sides`·`destructive_operation`·`path_conflict`. 층1
  planner 1b(resource_conflict 다음, `requests.overlap_candidates`) = Conflict `path_conflict` + 이벤트 `path_conflict`(상대 요청 id·내
  쪽 경로만), 배치 자식도 같다(항목 Failed). 층2 stepper `_overlap_hold`(sync·rm 의 exec_preflight·실행 제출 전, scan 의 실행 제출 전;
  `requests.overlap_gate_jobs`·`overlap_level`·`overlap_blocks`) = 보류(실패 아님, `path_overlap_hold`/`path_overlap_released`,
  `worker_pool.overlap_hold`) + 보류 틱마다 `data_jobs.touch_held`(claim_steppable 이 updated_at 순 LIMIT 10 이라 보류 잡이 클레임
  자리를 채우는 교착 방지). 포탈: `RequestActivity.conflictRequestId` → 전이 이력의 「겹친 요청 <앞8>」 링크(`TransitionsCard`·
  `Timeline`). 계약은 ARCHITECTURE §4 「rm 경로 겹침 잠금」(새 경로가 exec_preflight·실행을 내면 `_overlap_hold` 필수).
- **A3 결과 파일 자동 만료**(파일만, 행은 남김, 기본 1500일): 새 테이블 없이 아웃박스 `request_purges.kind`(NULL = 요청 삭제,
  'expiry' = 만료, 그 밖 = 변조 → purge_row_invalid)와 `data_jobs.artifacts_expired_at`(+ 부분 인덱스 `idx_data_jobs_expiry`). 컨트롤러
  `artifact-expiry` 루프(`artifact_expiry.ArtifactExpirer`, 300초, purge_runner 있을 때만, 대기 만료 행 ≤ 50 = `BACKLOG_MAX`)가
  `RequestPurgesRepository.expire_artifacts`(잡·요청 둘 다 종단 ∧ updated_at < now − N일, PG `FOR UPDATE OF d SKIP LOCKED`)로 표시만
  하고, 지우기는 request-purge 의 같은 길(안전 확인 `invalid_expiry_targets` — 요청 삭제의 거울, 끝 `finish_expiry`; 실패 사유
  `artifact_expiry_target_invalid`). 완료 이벤트 `artifacts_expired`(component `artifact-expiry`, request_id = 그 요청 — 요청이 살아
  있다), 고르기 요약 `artifact_expiry_queued`(request_id NULL). 만료 정리 중인 요청을 지우면 `_enqueue` 가 요청 삭제 행으로 바꾼다.
  API: 목록 200 `{entries:[], truncated:false, expired_at, retention_days}`, 뷰·다운로드 410 `artifact_expired`(소유 확인 뒤 — 남의 잡은
  404), `GET /api/admin/artifact-base` 의 `artifact_retention`, 삭제 툴바 `status()` 는 만료 행 제외, base 잠금은
  `pending_count(include_expiry=False)`. 설정 `DMS_ARTIFACT_RETENTION_DAYS`(`_SERVER_INT_KEYS`, 기본 1500, 0 = 끔, 음수 기동 거부,
  20-config.yaml "1500"). 포탈: 요청 상세(JobStages) 「결과 파일 만료됨(보존 N일, 날짜 KST)」(`expiryText`)·아티팩트 경로 화면 보존 줄.
  과제 문구의 「하루 한 번」 대신 5분 간격 + 작은 상한으로 바꿨다(인덱스로 싸게 고르고, 첫 배포의 오래된 이력을 하루 ~1.4만 건씩 따라잡는다).
- **A4 배치 단위 삭제 상한**: `request_purges.MAX_BATCH_DELETE_CHILDREN` 1000 → 2000(`MAX_BATCH_DELETE_ITEMS` 10000 그대로 — 목록
  요약의 cap 도 따라 2001 에서 멈춘다). 포탈 거울 `batchGroups.MAX_BATCH_CHILDREN`·api.ts `batch_delete_too_large` 문구, 세 곳 대조
  `tests/test_batch_delete_limits_mirror.py`. 대가: 최대 배치 하나가 RLock 을 ~0.7–2초 쥐어 readiness 한 번을 놓칠 수 있다(연속 3회가
  아니면 파드는 빠지지 않는다 — request_purges 주석·ARCHITECTURE §5). 청크 삭제는 BACKLOG 에 남김.
- **A5 비 UTF-8 이름 = 카운터만 + C 「dscan 총바이트 집계」**: 포크 `chahwansong/mpifileutils` ae781bd(3c041e0 직계 후손 — nsync·
  `--chown` 수정 유지)의 dscan 이 `summary` 에 `total_bytes`(일반 파일 st_size 합, 정확값 — 시간 히스토그램 bytes 합과 같다)·
  `invalid_name_entries`(이름 d_name 이 엄격 UTF-8 이 아닌 항목 수, broken 사유·`broken_paths_total` 에 더하지 않음)를 쓴다.
  `Dockerfile.mpifileutils` `MPIFILEUTILS_REF` = ae781bd(`tests/test_k8s_submit.py` 의 고정값 집합에 3c041e0 = 총바이트 없음).
  실 사용량 우선순위 = `summary.total_bytes`(엄격 정수) → 시간 히스토그램 합 — 러너 `parsers.parse_scan_counts`(→ bytes_count)와 서버
  `routes_scan_paths._report_total_bytes`·`_summary_count`(scan_path_stats·request_scan_stats·`routes_usage._project_report` 공용)가 짝.
  새 응답 필드 `invalid_name_entries: int | null`(키 부재 = null), storage-totals 에 `{sum, known, unknown}`(`TOTAL_SUMMARY_KEYS`).
  요약 캐시는 스키마 그대로 — summary 에서 다시 뽑아 옛 캐시 행도 새 값을 낸다. 포탈 `lib/scanCounters.ts`(배치 항목 scan 리포트·
  사용량 상세·합계 각주·CSV 고정 열, 크기 칸의 정확 바이트 title — `KpiTile.title`).
- **A6 Pure FlashBlade**: 백로그 유지(BACKLOG (h)).
- **C1 요청 상세의 단건 삭제**: 「작업 삭제」(관리자·종단 요청만 — 배치 자식·비종단 잡이면 잠기고 이유, 배치 자식은 「이 배치의 작업
  보기」). `DeleteRequestsDialog` 재사용(`triggerLabel`, `onDeleted(r)`), `useDeleteRequests({onDeleted})` 의 onDeleted 는 캐시 제거
  **전에** 불려 404 화면을 거치지 않고 `navigate('/jobs', {state: {notice}})` → 작업 목록 `NavNotice`(role=status). 서버가 제외하면
  상세에 머물러 「삭제하지 않았습니다 — <사유>」.
- **C2 배치 기록 삭제의 감사·CAS**: `BatchesRepository.delete(batch_id, *, actor)` — 한 트랜잭션에서 batch_items → batches 잠금
  (`_lock_items_then_batch`) → 종단 재확인 → `DELETE … AND status IN ('Completed','Cancelled')` + 감사 `('batch','delete_record')`
  (before = 이름·상태·연산·항목·집계·요청자·생성 시각). 라우트 `DELETE /api/admin/batches/{id}` 는 `require_session_admin`(토큰 403
  `admin_session_required`), CAS 패배 409 `batch_record_changed`, 그 사이 삭제 404 `batch_not_found`.
- **C3 배치 CSV 백엔드화**: 순수 모듈 `src/dms/batch_csv.py` + 라우트 셋(require_admin, 읽기 전용, 유지보수 무관) —
  `POST /api/admin/batches:parse-csv`(생성과 **같은** 줄 판정: `build_data_payload`·`batch_storage_mixed`·새 `domain.rm_target_overlaps`
  (+ `related_line`) → 오류 없으면 `validate_batch`; 오류 `{line, row, reason_code, message, related_line}` 최대 200 + `error_count`;
  413 `batch_csv_too_large`(2 MiB)·422 `batch_csv_too_many_rows`·줄 사유 `batch_csv_bad_row`·`batch_csv_malformed`),
  `GET /api/admin/batches/csv-template`(get_batch 보다 먼저 등록 — 순서 테스트), `GET /api/admin/batches/{id}/items.csv`(BOM·수식 방어
  `'` — 해석이 같은 집합을 걷어 왕복). `_reject_overlapping_rm_targets` 는 `rm_target_overlaps` 를 쓴다(422 동작 그대로). 포탈
  `lib/csv.ts` 는 얇은 클라이언트(`parseItemsCsv` 삭제, `useParseBatchCsv`), BatchCreate 「테이블에 반영」·파일·「템플릿 받기」,
  BatchDetail 「CSV로 전체 교체」(0.4초 디바운스, 서버 items 를 그대로 PUT)·「CSV 내보내기」.
- **C4 배치 생성의 스토리지 드롭다운**: `features/batches/BatchStoragePicker.tsx`(파일시스템 / PV·파일시스템 / PV optgroup, 「이름
  (backend)」·관리자 전용 표시, 고른 뒤 백엔드·종류·관리 디렉토리 캡션, 목록 밖 값 「(목록에 없음)」 경고, 빈 목록이면 스토리지
  화면 링크). aria-label 은 그대로(e2e 무수정).
- **D1 빌드 파드 GC 200건 창 고착**: `builds.pods_gc_at`(CREATE + `_ensure_columns`) + 부분 인덱스 `idx_builds_gc`;
  `BuildsRepository.terminal_older_than` 은 표시 없는 것만·좁은 SELECT(log_text 제외), `mark_pods_gc`; `pod_gc` 는 잡과 같은 규칙(두 파드
  terminate 성공(404 = 성공)이면 찍고, 실패면 1시간 뒤 — 빌드 재시도 기억은 잡과 따로, 공용 `_back_off`).
- **D2 빌드 삭제 시 파드 누수**: `routes_builds.delete_build` 가 404·409 검사 뒤 `pods_gc_at` 이 없고 build_runner 가 있으면
  `_terminate_build_pods`(ref 별 격리)로 빌드·프로브 파드를 먼저 지우고, 하나라도 실패면 502 `build_pod_cleanup_failed`(행·감사 그대로).
  `BuildsRepository.delete` docstring 정정, `_detail` 은 `pods_gc_at` 을 싣지 않는다. RBAC 무변경(api Role pods delete).
- **D3 securityContext 드리프트 배지**: `rollout_status.security_context_view`·`security_drift`(SECURITY_FIELDS 8개, 형 이상 = 그
  컨테이너 None), `normalize_*` 의 `security_contexts`(스텁 {}), `manifest_tags.manifest_security_contexts`(SECURITY_COMPONENTS =
  dms-api·dms-controller, fail-soft — 블록 부재도 None), `GET /api/admin/releases/targets` 의 `security_drift`(None / [] / 목록, 컴포넌트당
  observe 1회). 포탈 `ReleasesPage` 「보안 설정 드리프트」 배지 + 필드별 「라이브 X · 매니페스트 Y」 + 「포탈 롤아웃은 이미지만 바꿉니다」
  안내(null·[]·구 서버는 아무것도 안 그림). 대시보드에는 넣지 않았다.
- **사유 코드**(reasonCodes.json·api.ts 양쪽): `path_conflict`, `artifact_expired`, `artifact_expiry_target_invalid`,
  `build_pod_cleanup_failed`, `batch_record_changed`, `batch_csv_too_large`, `batch_csv_too_many_rows`, `batch_csv_malformed`,
  `batch_csv_bad_row`. 문구 보강: `admin_session_required`(배치 기록 삭제), `batch_delete_too_large`(2000),
  `invalid_storage_owner`·`storage_owner_changed`·`pv_owner_not_applicable`(직접 경로).
- **스키마**: `builds.pods_gc_at`, `request_purges.kind`, `data_jobs.artifacts_expired_at`(+ `_ROW_COLUMNS_SANS_DIAG`), 인덱스
  `idx_builds_gc`·`idx_data_jobs_expiry` — `test_migrations` 의 ensure 쌍 수·인덱스 집합 갱신. 새 테이블 없음 → 요청 삭제 목록 무변경.
- **테스트**: 새 파일 `test_path_overlap`·`test_planner_path_conflict`·`test_stepper_overlap_hold`(2~4잡 기다림 그래프 소진 — 고리
  없음·진행 보장)·`test_artifact_expiry`·`test_api_artifact_expiry`·`test_batch_csv`·`test_api_batch_csv`(기록 삭제 세션·감사·CAS 포함)·
  `test_batch_delete_limits_mirror`·`test_direct_dest_owner`, 보강 `test_api_builds`·`test_builds_repo`·`test_pod_gc`·`test_rollout_status`·
  `test_api_releases`·`test_manifest_tags`·`test_job_runner_parsers`·`test_api_request_scan_stats`·`test_api_scan_path_stats`·
  `test_api_usage_analysis`·`test_usage_metrics`·`test_k8s_submit`·`test_migrations`·`test_repo_request_purges`·`test_cli`
  (`artifact-expiry=ok`)·`test_control_plane_static`(만료 모듈도 FS 무접촉 그물)·`test_api_user_storages`. 포탈 vitest
  `RequestDetail.delete`·`RequestActivity.pathConflict`·`scanCounters` 새 파일 + 각 화면 테스트 보강, e2e `14-detail-delete-batch-csv`
  (상세 단건 삭제 → 목록 알림 → 404; 템플릿 → 서버 해석 오류 → 수정 반영 → 생성 → 내보내기 → 재해석 왕복; 토큰 기록 삭제 403 → 세션
  200 + 감사 행)·`11-batch-rm`(겹침 오류는 「반영할 수 없는 줄」 목록).
- **구현 중 발견 → BACKLOG**: claim_steppable 의 클레임 굶주림(단순 폴링이 updated_at 을 밀지 않는다), 깨진 JSON 행 하나가 stepper
  틱 전체를 멈춤, 만료된 scan 리포트를 읽으려는 화면(스캔 경로 통계·사용량), 단건 scan 요청 상세엔 잘못된 인코딩 이름 수가 안 보임,
  포개진 다른 이름 스토리지의 겹침·둘째 관문의 오래된 판정.
- **리뷰(독립 4차원 + 반박 검증)**: 확인 2건(low) 수정 — 배치 실행 설정 chown 칸 안내가 「비우면 소스 소유 보존」(pv/both 목적지는 이제
  아님), CSV 를 내보내고 다시 읽으면 정말 `'=` 로 시작하는 경로의 `'` 가 사라짐(내보내기가 `'` 로 시작하는 칸에도 `'` 를 붙이고 읽기가 하나
  걷는다 — 왕복 테스트). 반박: 큰 rm 배치 미리보기 중 겹침 후보 조회의 O(N²)(유한·실패 없음 — BACKLOG 최적화 후보), 만료가 scan 리포트를
  지워 사용량 이력이 줄어드는 것(의도 — 기본 1500일, 실 사용량은 bytes_count 로 물러남; 만료 전 요약 캐시는 BACKLOG 후속), 배치 기록 삭제와
  `:rescan` 경합(재열기 쪽의 기존 성질).
- 검증: 백엔드 전체 3,963 통과 + 부하 플레이크 1(request_purger 초 경계 — 같은 파일 단언 2곳을 고쳐 62 통과), vitest 1,566·`tsc -b`·e2e 29
  통과(새 스펙 2).

**테스트베드 적용 — d6**(2026-10-27, 소스 99dd8f5): 포탈 빌드 d6(이미지 3종 + 잡 이미지 — mpifileutils ae781bd, 225초) → 릴리스(agent →
job-image·controller·api) → guard → apply -k(`DMS_JOB_IMAGE` d6·`DMS_ARTIFACT_RETENTION_DAYS` 1500) → migrate → rollout restart. 파드 전부 d6·
재시작 0·오류 0, `verify.sh --context dmsk8s` PASS 4. 라이브 실증(전부 기대대로):
- **rm 경로 겹침 잠금 — 층1(planner)**: P1 alice PV sync(확인 대기 = 잠금 보유) → P2 소스 하위 rm Conflict `path_conflict`(상대 P1·source)
  · P3 목적지 조상 rm Conflict(destination) · P4 성분 접두만 같은 `src2` 는 겹침 아님(계획) · P5 mason **직접 경로** rm 이 PV 주소 sync 와 같은
  스토리지로 비교돼 Conflict · P6 sync↔sync 는 계획 · P7 같은 대상 rm 두 번째는 종전대로 `resource_conflict` · P8 P1 취소 뒤 같은 rm 은
  Succeeded(`src/sub` 만 삭제, `keep` 유지) · P9 rm↔scan 대칭(scan Conflict) · P10 남의 요청과 겹친 alice rm Conflict, 그 요청은 alice 에게 404.
  **배치**: B2 rm 배치 항목 0 이 확인 대기 sync(B1)와 겹쳐 Failed `path_conflict`, 항목 1 은 미리보기 · B3 B1 취소 → 항목 재실행 → PreviewReady
  · B4 확인 대기 rm 배치 자식이 sync 배치 자식·단건 rm 을 막음 · B4b 배치 취소로 잠금 해제 → sync 배치 자식이 단건 rm 을 막음 · B5 배치 안
  겹침은 종전대로 422 `batch_rm_targets_overlap`.
- **층2(실행 직전 보류 — 이 기능 전에 계획된 행을 DB 로 흉내)**: L1 2.4 GB sync 실행 중 그 소스 안을 지우는 rm 이 exec_preflight 전에
  보류(`path_overlap_hold` 1건, conflict_level 2, 파드 없음) → sync Succeeded 뒤 `path_overlap_released` → rm 진행. **sync 결과 6파일 온전**
  (f1 md5 원본 일치), 소스 f1 은 그 뒤에 삭제. L3 나중 제출 R2 를 먼저 확인해도 앞선 R1 이 먼저 진행, R2 보류 → 해제 → 대상 부모가 없어
  `parent_not_writable` 종단 — 교착 없음.
- **직접 경로 결과 소유**: k8s-pv-nfs(pv, 기본 1600) → 결과 1600:1600·setuid 4755 → 755, k8s-pv-gpfs(both, 기본 1700) PV 루트 아래 →
  1700:1700(`pv_root` 기록), 같은 스토리지 PV 루트 밖 → 규칙 `none`·소스 소유 1000:1000 보존, cephfs-dms(filesystem) → `none`. 배치:
  미리보기 뒤 기본값 변경 → 배치 확인 시 자식 Rejected `storage_owner_changed`·목적지 미생성, 그대로면 1650:1650.
- **결과 파일 자동 만료**: 희생 잡(A1a) 종단 시각을 2022 로 → 루프 1회 `queued 1` → 아웃박스 kind expiry k8s → purging → 약 45초 뒤 소멸.
  잡 디렉터리·trash 없음, base 항목 61 → 60, 행 수(잡 66·전이 660·요청 77) 불변, 잡은 Succeeded·files_count 5 유지, 이벤트
  `artifact_expiry_queued`·`artifacts_expired`(request_purged 없음). API 목록 200 `{entries:[], expired_at, retention_days:1500}`·파일 410
  `artifact_expired`·남의 잡 404, 관리자 현황 만료됨 1건, 재실행 `queued 0`(멱등).
- **dscan 카운터**: 잘못된 UTF-8 이름 3개(파일 2·디렉터리 1 — 자식은 미가산)·한글 이름 포함 트리 scan → `summary.total_bytes` 1,005,115·
  `invalid_name_entries` 3·broken 0(카운터만), 잡 bytes_count 1,005,115.
- **배치 기록 삭제**: 토큰 403 `admin_session_required`, 진행 중 배치 409 `batch_not_deletable`, 끝난 배치 200 + 감사 행 `('batch',
  'delete_record')`(이름·상태·건수). **CSV**: parse-csv(BOM·빈 줄·따옴표 안 쉼표·`'=` 걷기, 절대경로 줄 `unsafe_path`), items.csv(BOM·
  Content-Disposition), 템플릿. **빌드**: pod-gc 가 24시간 넘은 빌드에 `pods_gc_at` 표시(재시작 뒤 첫 판은 루프 리스(간격×3=30분)가
  옛 파드 것이라 그만큼 늦다 — 기존 성질), k3 빌드 기록 삭제가 남은 빌드·프로브 파드 2개를 먼저 정리(토큰 403). **securityContext
  드리프트**: 평소 api·controller `[]`(거짓 경보 없음) → controller 의 readOnlyRootFilesystem 을 false 로 패치 → `[{field:
  readOnlyRootFilesystem, live:false, manifest:true}]`·릴리스 화면 「보안 설정 드리프트」 배지(오버레이 재적용으로 원복).
- **실 Chrome**: 드리프트 배지·「겹친 요청 8fa26065」 링크·「결과 파일 만료됨(보존 1500일, 2026-10-27)」·「목적지 스토리지 기본 소유자
  1600:1600(직접 경로)」·사용량 분석 「잘못된 인코딩 이름 3개 이상」(옛 리포트가 섞여 「이상」)·배치 생성 스토리지 드롭다운(이름 + 백엔드)·
  템플릿 링크·배치 CSV 내려받기·요청 상세 단건 삭제 → 목록 알림. 단건 삭제 직후 지운 요청의 재조회 404 두 건이 콘솔에 남아
  (`useDeleteRequests` 가 화면 이동과 같은 틱에 캐시를 지움) — d7 의 `setTimeout 0` 지연은 라우터 전환(트랜지션) 커밋보다 먼저 돌아
  그대로 남았고, d8 에서 캐시 제거를 **상세 언마운트 때**로 옮겨(`onDeleted(r, drop)`) 실 Chrome 404 0건. e2e 14 에 「삭제 뒤 그 요청의
  404 응답 없음」 단언을 더했다(옛 동작으로 되돌리면 실패하는 것을 확인). d7 적용이 드리프트 실증용 controller 패치도 원복(배지 0).
- 실증 데이터 삭제, 스토리지 기본 소유 0:0 원복. nsync 는 이번 변경과 무관해 다시 돌리지 않았다. 배치 삭제 상한 2000 은 서버·포탈 대조
  테스트로만(2000개 배치를 만들지 않았다).

### ✅ 스토리지 기본 소유자(uid:gid) — PV 목적지 sync 결과 소유 — **완료·실증**(2026-10-26, d5)

사용자 결정(BACKLOG (l) 의 후속, 원문): 「각 k8s 스토리지 항목마다 기본 uid,gid 값을 같이 등록하도록 해 (기본 root:root). 그래서 sync
목적지가 pv일때 해당 pv의 스토리지 기본 uid gid로 chown 셋업하도록 해줘. 사용자는 항상 자동으로 셋업되도록 해. 단, 운영자는 옵션으로
pv 의 루트 소유자로 chown 을 둘수 있게 해.」 — 같은 날 출하한 「결과 = 잰 목적지 PV 루트 소유자」(아래 항목)의 **기본값을 바꿨다**: PV
목적지 sync 의 결과 소유 기본은 목적지 스토리지에 등록한 uid:gid 이고, PV 루트 측정은 특권 운영자 옵션으로만 남는다. 현재 동작의 지도는
ARCHITECTURE §7 「PV 목적지 root sync 의 결과 소유」.

- **스키마**: `storages.default_uid`·`default_gid` — CREATE TABLE 에 `BIGINT NOT NULL DEFAULT 0`(PG int4 는 2^31 에서 22003 —
  유효 범위가 0..4294967294 다), `_ensure_columns` 에 BIGINT 두 칸, `_backfill_storage_default_owner`(기배포 행 NULL → 0, `IS NULL` 만
  — 멱등, 칸마다 따로). CREATE 블록 SQL 주석에는 `uid:gid` 대신 `uid·gid` 를 쓴다(콜론 뒤 이름은 자리표시자 그물
  `test_no_migration_statement_smuggles_a_named_placeholder_in_comments` 에 걸린다).
- **정규화 하나**: `repositories.k8s.storage_default_owner(row) -> "uid:gid" | None` — NULL = 0(「컬럼 이전 행 = 사용자 지정 기본값」이라는
  정의, truthy 축약 아님), bool·정수 아님·음수·`OWNER_ID_MAX`(4294967294 = `execution_manifests._OWNER_ID_MAX`, 테스트로 대조) 초과는
  None(fail-closed — 0 으로 뭉개지 않는다). 화면·해석·제출·재확인이 전부 이 함수로 읽는다.
- **저장소·API**: `StoragesRepository.create(default_uid=0, default_gid=0)` 는 늘 명시 정수, `update(None = 유지)` 는 칸마다 **잠근 행**의
  현재 값을 다시 쓴다(옛 클라이언트·목록 화면의 사용 범위 PUT 이 보내지 않는다; 이미 저장된 무효 값을 고쳐 쓰지 않는다). 범위·형 밖은
  `invalid_storage_owner`(감사 행에 두 칸이 실린다). 라우트는 `StrictInt`(`"1500"`·`true` 를 강제 변환하지 않는다 — 형 오류는 FastAPI
  기본 422 목록, 범위 밖은 422 `invalid_storage_owner`), 응답(목록·등록·수정)에 `default_uid`·`default_gid`(NULL = 0, 정수 아닌 변조 값은
  None, 범위 밖 정수는 그대로 — 무엇이 틀렸는지 보이게)와 `default_owner`. 종류와 무관하게 검증·저장한다(pv·both 에서만 쓰인다).
  `/api/user/storages`(USER_FIELDS 계약)는 그대로 — 사용자는 볼륨 API 로 본다.
- **해석**: `resolve_volume` 이 `storage_owner` 를 늘 싣는다(PV 를 담는 스토리지에 매핑됐을 때만 값, 경로 미해석이어도 실림; 변조 값은
  None 이지만 `selectable` 은 막지 않는다 — 원본으로는 쓸 수 있다). `/api/user/k8s/volumes`·`/api/admin/k8s/volumes`·`resolve_pvc`(제출·
  재확인)·StorageClass 화면 행으로 그대로 흐른다. `k8s_access.resolve_side` 반환에도 `storage_owner`.
- **제출**(`routes_requests.submit`): 바디 `pv_owner: Literal["storage","pv_root"] | None`(강제 변환 없음). 게이트 순서 — 세션 게이트 바로
  뒤(PV 해석 전) 422 `pv_owner_not_applicable`(sync 아님·`destination_pv` 없음·chown 과 함께), 비면제 블록의 chmod 검사 다음 403
  `pv_owner_root_not_allowed`, 맵핑 게이트 뒤·sync 쌍 전에 **모든** PV 목적지 sync 의 `destination_pv` 블록에 `owner_rule`(생략 =
  "storage")을 쓰고 "storage" 면 해석된 기본 소유자를 `owner` 로 얼린다(무효면 409 `invalid_storage_owner`). 운영자가 명시 chown 을
  주면 규칙을 기록하지 않는다(결과는 chown 값 — 쓰이지 않는 스토리지 값을 얼리면 그 값의 변경·무효로 거짓 거부된다, 리뷰). 소스 블록·직접
  경로 목적지·rm·scan 의 payload 는 이전과 같은 모양.
  `_resolve_locations` 는 (위치, 블록, 쪽별 기본 소유자) 3-튜플.
- **재확인**(`k8s_access.recheck_request` → `_recheck_owner_rule`, planner 3a·컨펌 공용 — planner·`routes_jobs` 는 무수정): 블록에
  `owner_rule` **키가 있으면** 목록(`OWNER_RULES`) 밖 = `pv_owner_not_applicable`(변조), "pv_root" + 비면제 = `pv_owner_root_not_allowed`,
  "storage" 는 기록 `owner` 가 목적지 스토리지의 **현재** 기본 소유자와 같아야 한다(아니면 `storage_owner_changed`; 명시 chown 이 실린
  요청은 대조하지 않는다 — 결과가 chown 값).
  설계보다 엄격하게 정한 것: 현재 값이 무효면 `storage_owner_changed` 가 아니라 `invalid_storage_owner`(기록값도 None 이면 `None == None`
  으로 통과하는 구멍 + 문구가 「관리자에게 다시 저장 요청」으로 정확). **키가 없는 블록 = 배포 전 옛 행**은 새 검사 없이 종전(측정)대로
  — 부재를 거부로 강화하지 않는다.
- **실행**: `JobSpec.result_owner_rule`. stepper `_dest_pv`·`_owner_rule`(규칙이 문자열이 아니면 None — 측정 갈래, 재확인이 이미 끊음)
  하나로 `_build_spec`(storage → 블록 owner, 그 밖 → `worker_pool.dest_pv_owner`; 형식이 틀리면 None → 빌더 ValueError → submit_failed)과
  `_pv_owner_required`(규칙 ≠ storage 추가 — 빌더 측정 술어와 짝)를 판정한다. `execution_manifests`: 적용 술어 `pv_dest_owner_applies`
  (`_auto_chown` — 두 규칙 모두 `--chown <result_owner>`, **0:0 이어도 `--chown 0:0`**: 포크가 --chown 이 있으면 일반 파일 setuid/setgid 를
  지운다)와 측정 술어 `pv_owner_measured` = 적용 ∧ 규칙 ≠ storage(`_with_pv_owner` 만) — storage 규칙의 preflight 는 측정 이전과 바이트
  동일(봉쇄만), `pv_owner_unknown`·`pv_owner_changed` 는 pv_root·옛 행에서만 난다.
- **포탈**: 스토리지 등록·수정 창의 「기본 소유(uid:gid)」 칸 묶음(aria 「기본 소유 uid」·「기본 소유 gid」, 기본 0, 숫자만 0..4294967294 —
  빈 칸도 오류, PV·둘 다 종류에서만 보이고 싣는다; 정수 아닌 저장값은 빈 칸으로 시작해 다시 적게), 목록의 종류 배지 아래 둘째 줄
  「기본 소유 1500:1500」·「기본 소유 0:0(root)」·값 오류(빨강)·모름(옛 응답) — 같은 칸이라 e2e 칸 수 그대로, 사용 범위 PUT 무변경. 단일
  작업: 사용자에겐 조작 없이 소유권 안내가 「결과는 목적지 스토리지 기본 소유자 1500:1500 로 맞춰집니다」, 특권 운영자(`me.unrestricted`)
  ·PV 목적지 sync 에만 「결과 소유」 선택(`Segmented` 에 `disabled` 추가 — 「스토리지 기본(uid:gid)」 기본 / 「PV 루트 소유자」, chown 을
  채우면 잠기고 이유를 말한다, 직접 경로 목적지면 없다) — 바디 `pv_owner: "pv_root"` 는 고를 때만(기본 바디 무변경, 테스트로 고정). 목적지
  스토리지 저장값이 무효면 제출을 잠근다(서버 409 미러, pv_root 는 해당 없음). PV 루트 자체가 목적지면 경고가 「PV 루트 디렉터리 자체가
  스토리지 기본 소유자 … 로 chown」(막지 않음, `LocationField.pvRootDestNote`). 요청 상세 「목적지 소유」: storage → 「목적지 스토리지 기본
  소유자 <v>」, pv_root → 「목적지 PV 소유자 <잰 값>(PV 루트 기준 — 운영자 지정)」, 규칙 없는 옛 행 → 종전 문구(바이트 동일); 규칙 값이
  이상하면 행 생략. 확인 창에 sync 의 「결과 소유: …」 한 줄(RequestDetail → JobCard → JobStages → ConfirmDialog). 요약 실행 권한
  `PRIV_ROOT_SYNC_PV`(스토리지 기본)·`PRIV_ROOT_SYNC_PV_ROOT`(운영자 지정). 공용 `lib/syncOwnership` 에 `validOwner`·`ownerText`(0:0 →
  「0:0(root)」)·`OWNER_ID_MAX`·`PvOwnerRule`, `k8sTerms.storageOwnerOf`.
- **사유 코드**(reasonCodes.json·`REASON_MESSAGES` 양쪽): 새 `invalid_storage_owner`(등록·수정 422 + 제출·재확인 409 공용)·
  `pv_owner_not_applicable`·`pv_owner_root_not_allowed`·`storage_owner_changed`. 문구 변경: `chown_not_allowed` 꼬리 「결과는 목적지 스토리지
  기본 소유자(uid:gid)로 자동으로 맞춰집니다」, `pv_owner_unknown`·`pv_owner_changed` 는 「결과 소유를 PV 루트 소유자로 고른 sync」로 범위를 좁힘.
- **범위 밖(동작 무변경)**: 배치와 운영자의 직접 경로 sync 는 종전대로 소스 소유 보존(BACKLOG ❓ 「직접 경로 목적지가 PV 루트 아래일 때」에
  선택지 (4) 「그 스토리지의 기본 uid:gid」를 더했다).
- **테스트**: 새 `tests/test_storage_default_owner.py`(36 — 헬퍼·저장소·API·응답 정규화·해석·볼륨 API·SC 화면), `test_migrations`(그물 개수
  46 → 48 두 곳, CREATE 선언 BIGINT NOT NULL DEFAULT 0 소스 고정, 옛 DB ALTER·백필·멱등·4294967294 저장 — 방언 구분), `test_k8s_submit`
  (사용자 pv_root 403·운영자 pv_root·적용 밖 422·강제 변환 없음·무효 저장값 409·미등록 = 0:0·옛 블록 그대로 실행·사용자 PV sync 는 재지 않고
  스토리지 기본으로 chown·운영자 pv_root 는 잰다, planner·컨펌 재확인 표에 기본값 변경·pv_root 변조·owner 변조/삭제), `test_pv_owner_chown`
  (술어 둘 분리, 규칙별 argv·형식 거부, storage 0:0 도 `--chown 0:0`, storage 규칙 preflight = 봉쇄만 스크립트(실 sh), stepper 의 storage
  규칙 흐름 — 재지 않음·exec_preflight 무측정·잡 행 잰 값 무시·기록값 형식 오류 fail-closed·명시 chown 우선, 측정 테스트는 pv_root·옛 행
  두 규칙으로 매개변수화), `test_execution_manifests`. 프런트 vitest 8 파일(syncOwnership·k8sTerms·requestSpec·RequestSpecCard·
  ConfirmDialog·SubmitJob.k8s·StorageDialog·StoragesList).
- 진행: 이해(독자 4) → 구현(스토리지 층·포탈 병렬 → 파이프라인 → 문서) → 독립 리뷰(4차원 + 차원마다 반박 검증) 워크플로. 리뷰가
  남긴 확인 3건(전부 low)을 고쳤다: ① 운영자 명시 chown 요청이 쓰이지 않는 스토리지 값을 얼려 그 값의 변경·무효로 거짓 거부(제출이 chown
  이면 규칙을 기록하지 않고, 재확인도 chown 이면 대조하지 않는다 — `test_operator_chown_does_not_freeze_the_storage_owner`) ② 「제출 뒤
  바꾸면 멈춘다」 문구 과장(대조는 계획·컨펌까지 — 이미 컨펌한 잡은 확인 창의 값으로 끝난다) ③ 「결과 소유」 버튼 라벨에 값이 들어가 768 폭에서
  도움말과 겹침(라벨 고정, 값은 도움말로). 반박된 것: `owner_rule` 키를 지운 변조 행이 측정 갈래로 가는 것(문서화된 옛 행 경로 — 그 DB
  기록자는 이미 requester 를 운영자로 바꿀 수 있다).
- 검증: 백엔드 전체 3,651 통과(리뷰 수정 전 스냅숏) + 수정 뒤 영향 파일 387 통과(`test_k8s_submit`·`test_pv_owner_chown`·스토리지·재확인·
  사유 코드·잡·planner), PG 모드(스토리지 147·마이그레이션 새 테스트), vitest 1,508·`tsc -b`·e2e 27 통과.

**테스트베드 적용 — d5**(2026-10-26, 소스 b31c627): 포탈 빌드 d5(이미지 3종 + 잡 이미지) → 릴리스(agent → job-image·controller·api) → guard
→ apply -k(DMS_JOB_IMAGE d5) → migrate(새 칸 + NULL 백필 — 스토리지 6개 모두 0:0) → rollout restart api·controller. 라이브 실증(목적지
dmsk8s2 lab-x results, PV 루트 1500:1500; 소스 dmsk8s team-a datasets 의 setuid 4755·setgid 파일 2755·setgid 디렉터리 2775 를 포함한 12항목):
- **기본(0:0)**: alice PV→PV sync(dsync) — 요청 `destination_pv` 에 `owner_rule: "storage"`·`owner: "0:0"`(소스 블록은 무변경), 잡에 잰 값
  없음(측정 안 함), 결과 12항목 전부 **0:0**, setuid 4755 → 755·setgid 파일 2755 → 755(`--chown 0:0` 의 비트 지우기), setgid 디렉터리 2775
  유지, 내용 일치. 사용자 볼륨 API 의 `storage_owner` 0:0.
- **스토리지 값 변경(1600:1600)**: 관리자가 k8s-pv-nfs 기본 소유를 바꾼 뒤 alice sync → 결과 12항목 **1600:1600**(같은 비트 처리).
- **게이트**: alice `pv_owner: "pv_root"` 403 `pv_owner_root_not_allowed`, rm 에 `pv_owner` 422 `pv_owner_not_applicable`.
- **운영자 옵션 PV 루트**: mason `pv_owner: "pv_root"` → 스토리지 값이 1600 인데 결과 **1500:1500**(잰 PV 루트), 같은 비트 처리.
- **컨펌 전 변경**: alice 요청이 1600 으로 얼어 ConfirmPending → 관리자가 1700 으로 → 컨펌 409 `storage_owner_changed`·요청 Rejected·목적지
  미생성.
- **운영자 명시 chown**: mason chown 1234:1234 → 결과 1234:1234(스토리지 값과 무관).
- **실 Chrome**: 스토리지 목록 PV 행 「기본 소유 1700:1700」(파일시스템 행엔 없음)·수정 대화상자 uid/gid 칸(4294967295 → 「0~4294967294
  정수」 오류), alice 제출 안내 「목적지 스토리지 기본 소유자 1700:1700」·「결과 소유」 선택 없음, mason 「결과 소유」(기본 스토리지 기본, 값은
  도움말 — 1440·768 폭 겹침 없음) → PV 루트 소유자로 바꾸면 요약 「목적지 PV 루트의 소유자·그룹(점검 뒤 확정 — 운영자 지정)」, 요청 상세
  alice 「목적지 스토리지 기본 소유자 0:0(root)」·mason 「목적지 PV 소유자 1500:1500(PV 루트 기준 — 운영자 지정)」, 확인 창 「결과 소유: 목적지
  스토리지 기본 소유자 1700:1700」, 콘솔 오류 0. `verify.sh --context dmsk8s` PASS 4.
- nsync 는 라이브로 다시 돌리지 않았다(argv 는 두 rank 공통이고 규칙은 값만 바꾼다 — 단위 테스트가 nsync 역할 preflight·argv 를 고정, `--chown`
  의 nsync 동작은 d3 에서 실증). 실증 데이터는 지우고 스토리지 기본 소유는 0:0 으로 원복.

### ✅ LDAP 코드 삭제·정리(dms-k8s) — k8s 신원 모드 전용 — **완료·실증**(2026-10-26, d4)

사용자 결정 (d) 「LDAP 코드 삭제 및 정리」(BACKLOG 설계 §10). dms-k8s 는 2026-10-21 부터 LDAP 을 쓰지 않았지만 코드에는 ldap 모드
(기본값)·리졸버·노드 신원 프로브·nslcd·보조 그룹이 그대로 있었다. 일곱 커밋, 합계 약 +2.2k / −11.0k 줄(브랜치 `k8s/rbac-pv`, push 안 함):

- `7fe2497` **모드 단일화**: `Settings.identity_mode`·`k8s_access.identity_mode/k8s_mode` 삭제, 안전장치는 무조건(`DMS_IDENTITY_MODE` 가
  k8s 아님·`DMS_LDAP_URI` 있음·`DMS_ALLOW_PRIVILEGED_REQUESTERS=false` → 기동 거부). `identity.resolve_job_identity` 는 리졸버 없이
  통과 = 늘 root, **근거 없는 행(`PRIVILEGE_NEVER`)·목록 밖 생성자의 배치 자식은 낮추지 않고 `privileged_not_authorized`**. denylist 는
  이름 규칙만(`control.is_denied(requester, owner)`, 그룹 추가는 늘 422). planner 는 서킷·유예·노드 신원 대기·그룹 이벤트 없이 다시
  썼다(스냅숏 5키). 라우트(제출·잡·스캔 경로·배치·/me·k8s 개요)의 모드 분기를 상수화.
- `16c69c9` 7171b28 뒤 깨져 있던 `test_no_migration_statement_smuggles_a_named_placeholder_in_comments` 수정(리터럴 그룹 튜플을 이름으로 셈).
- `216b76b` **LDAP 신원 백엔드 삭제**: `identity_ldap.py`·`agent_directory.py`·`agent/directory.py`, stepper 의 LDAP 재확인 기계(큐 대기 재확인·
  보류 재시도), 로그인 신원 프로브, 노드 신원 프로브(`identities`·`identity_probe_targets` 하달)·nslcd 디렉터리 하달, placement 의 신원
  준비 검사, LDAP 설정 필드·`identity_supplementary_groups`·`identity_probe_ttl_seconds`·`planner_identity_grace_seconds`, pyproject `ldap`
  extra. `identity_probe_targets` 테이블은 빈 채로 남긴다(마이그레이션 주석).
- `68a9b31` **실행 계층 보조 그룹 삭제 + fail-closed**: preflight supplementalGroups·`DMS_JR_SUPP_GIDS`·그룹 물질화·자기검증·그룹 preflight 의
  base 검사·컨트롤러 정적 관문(`ArtifactBaseUnsafeAtStep`) 삭제. 러너 passwd 물질화(root 별칭 계정 — runuser·sshd 로그인)는 유지.
  **보안 리뷰(216b76b 의 MEDIUM fail-open)**: 재확인을 지운 뒤 비 root 신원·보조 gid 목록이 실린 옛/변조 행이 조용히 돌 수 있었다 —
  `stepper.identity_problem` 이 `non_privileged_identity`·`supplementary_gids_unsupported` 로 끊고(`identity_missing_at_step`, 제출 전),
  빌더도 `_reject_supplementary_gids` 로 거부(백스톱). root 행의 키 부재·None·[] 는 통과(진행 중 잡 보호).
- `a83ab2b` **배포**: Dockerfile.agent 에서 nslcd·libnss-ldapd·엔트리포인트 삭제(`ENTRYPOINT ["dms"] CMD ["agent"]`, root 유지), base
  `20-config` 의 LDAP 키·`DMS_IDENTITY_PROBE_TTL_SECONDS`·`DMS_PLANNER_IDENTITY_GRACE_SECONDS` 삭제 + `DMS_IDENTITY_MODE: "k8s"` 를 base 로,
  DaemonSet `dms agent` 직접 기동, 시크릿 예시 4키, testbed-k8s·prod 오버레이의 LDAP null 패치·`patch-agent.yaml` 삭제, prod render.sh 의
  `IDENTITY_SUPPLEMENTARY_GROUPS` 처리 삭제(옛 키는 알림 한 줄), ssc 오버레이 `deploy/overlays/testbed` 삭제(dms-ssc 소관). 태그 무변경.
- `439419b` **포탈**: SubmitJob·BatchCreate·k8s·스토리지·sync 쌍 화면의 ldap 분기, 보조 그룹 화면(`lib/groupCaveats`·잡 상세 행·단계 모델의
  재확인 보류), 노드 화면 identities·directory, denylist 그룹 추가(옛 group 행은 「평가 안 됨」·해제만), 사용자 sync 쌍 조회(`lib/syncPairs`)
  삭제. 계정 화면 「전체 PV·직접 경로 권한」. 은퇴 사유 코드 8개는 reasonCodes.json 에 남기고 문구를 「이전 버전…」으로(옛 행 표시).
  e2e 하네스는 k8s 전제(E3·E4 직접 경로 모드, E11 부여 = 맵핑 면제).
- 문서(이 커밋): ARCHITECTURE(불변식 12·신원 가드·§7 함정·§8 에이전트·신원), CLAUDE.md(k8s 신원 모드를 본문 규칙으로, 보조 gid 예외 축소),
  deploy/README(§2b·§2c·§13·스크립트 예시), BACKLOG(LDAP·보조 그룹 항목 「dms-k8s 해당 없음」). `_auto_chown` docstring 정정(비 root 갈래는
  빌더 방어 기본값).
- **삭제 뒤 리뷰 반영**(독립 리뷰 — 「모든 잡이 root」가 된 뒤 남은 틈):
  - **컨펌은 세션 로그인만**: `routes_jobs.confirm_job` 이 토큰 인증을 403 `confirm_session_required` 로 거부. 공유 토큰(모든 노드
    에이전트 보유, role admin)은 `_owned_job` 을 통과해 남의 미리보기를 사람 확인 없이 uid 0 실행으로 넘길 수 있었다 — 요청 행의
    `root_allowed` 재검사는 **요청자**의 인증만 본다. 조회(GET)는 토큰도 된다.
  - **배치 자식 판정 우선**: `identity.privilege_policy` 가 batch_id 를 run_as_root 보다 먼저 본다 — orchestrator 는 자식 payload 에
    run_as_root 를 싣지 않으므로 실려 있으면 변조이고, 세션만 보는 REQUESTED 로 특권 목록 판정을 건너뛰게 두지 않는다.
  - **잡 이미지 시스템 계정 이름 예약**(`domain.RESERVED_ACCOUNT_IDS` — daemon·bin·sys·sync·games·man·lp·mail·news·uucp·proxy·
    www-data·backup·list·irc·nobody·sshd): 실행 신원은 passwd 에 「이름 → 0:0」 별칭으로 물질화되는데 같은 이름이 이미지에 다른 uid 로
    있으면 러너가 exit 1 — 새 계정·가입 인증번호·`owner_username` 에서 422 `username_reserved`(로그인·재설정은 형식을 보지 않는다).
    root 는 이미지에서도 0:0 이고 특권 목록 기본 이름이라 제외. 포탈 「권한 기준 사용자」는 `pvLocation.isAccountId`(대조 테스트).
  - 문서: ARCHITECTURE 불변식 5·9·10(보조 그룹·비 root 잡 전제 서술 정리)·배선(리졸버 주입 삭제), deploy/README 테스트베드 표.

남긴 것: 러너 passwd 물질화·`getent hosts`, `PrivilegeNotRequestedAtStep`, owner_override/privilege_eligible/can_run_as_root, denylist 이름
규칙, chown_problem, artifact base 저장·3홉 검증(g+w·ACL·o+x), 설정 안전장치, 빈 `identity_probe_targets` 테이블, 은퇴 사유 코드(옛 행
표시), /me·개요의 `identity_mode: "k8s"` 상수(API 계약), 관리자 sync 쌍 화면. 옛 스냅숏의 `supplementary_*` 키·옛 에이전트 보고의
`identities`·`directory` 는 읽지 않는다(저장만). dms-ssc 의 LDAP 라인과 `deploy/testbed/dms-shared-fs.yml`·`docs/history/` 는 손대지 않았다.

검증: 백엔드 전체(병렬 6분할) 통과 — 단계별 3,821 → 3,628 → 3,580 → 3,534(9 skip; 지운 테스트만큼 감소), vitest 101 파일 1,483 통과,
`tsc -b` 통과, e2e 27 통과(실 브라우저). 리뷰 반영 뒤 최종(46c1264): 백엔드 전체 3,533 통과 + 수정한 픽스처 파일 43 통과(전체 실행이 찾은
회귀 — `test_api_k8s` 의 「바인딩 없는 사용자」 계정 이름 `nobody` 가 예약어에 걸림 → `nomap.user`), 부하 플레이크 `test_request_purger` 의
1초 경계 단언 수정, PG 모드 200 통과, vitest 1,484·tsc·e2e 27 통과(부하 중 한 번 SQLite 잠금으로 하네스 컨트롤러가 죽어 1건 실패 → 단독 재실행
통과).

**테스트베드 적용 — d4**(2026-10-26, 소스 46c1264): 포탈 빌드 d4(이미지 3종 + 잡 이미지, 195초) → 릴리스(agent → job-image·controller·api,
라이브 = 동봉 d4) → guard(변경 없음) → apply -k(렌더 차이는 ConfigMap 키 3개 삭제뿐 — `DMS_IDENTITY_PROBE_TTL_SECONDS`·
`DMS_IDENTITY_SUPPLEMENTARY_GROUPS`·`DMS_PLANNER_IDENTITY_GRACE_SECONDS`; 에이전트 `dms agent`·LDAP env 삭제는 d3 오버레이 패치로 이미 라이브)
→ migrate → rollout restart api·controller. 회귀 실증(전부 통과):
- **기동·보고**: 파드 전부 d4·재시작 0·로그 오류 0, ConfigMap 의 신원 키는 `DMS_IDENTITY_MODE` 하나, 두 노드 보고 신선(보고에 신원·디렉터리
  키 없음), 두 클러스터 첫 동기화 ok(06:09:20), 노드 화면 신원 열 없음, `verify.sh --context dmsk8s` PASS 4.
- **토큰 컨펌 거부**: alice 의 클러스터 간 PV→PV sync(dmsk8s team-a datasets → dmsk8s2 lab-x results, dsync)가 ConfirmPending — 공유 토큰
  컨펌 403 `confirm_session_required`·잡 그대로 → alice 세션 컨펌 200 → Succeeded 12항목. 결과 12항목 전부 1500:1500(PV 루트), setuid
  4755 → 755·setgid 파일 2755 → 755·setgid 디렉터리 2775 유지, 내용 일치(d3 와 같음).
- **rm·scan 게이트**: alice PV rm(drm) Succeeded(대상만 삭제, PV 루트·marker 유지), alice scan 403 `scan_unrestricted_only`, mason 의
  dmsk8s2 lab-y scan Succeeded.
- **배치 자식(IF_ELIGIBLE 우선)**: mason sync 배치 1항목(직접 경로) Previewing → PreviewReady → 배치 확인 → Completed, 자식 Succeeded·내용
  일치(직접 경로라 소스 소유 보존 — BACKLOG 의 열린 질문).
- **시스템 계정명**: 가입 인증번호 `daemon`·`nobody` 422 `username_reserved`, `root.test` 200. 라이브 계정 5개 중 예약어 0.
- **실 Chrome**: alice 요청 상세 「목적지 PV 소유자 1500:1500(PV 루트 기준)」, mason 「권한 기준 사용자」에 `daemon` → 형식 안내·`alice` → 안내
  없음, 노드·PV 탐색·사용자 권한·k8s 연동·배치 생성·계정·denylist·단일 작업(375 포함) 9화면 가로 넘침 0px·「LDAP/보조 그룹」 문구 0·콘솔 오류 0.
실증 데이터는 지웠다(PV 루트 소유는 픽스처 값 그대로).

### ✅ d3 라이브 실증 — PV 소유자 chown·setuid·namespaceAdmin·scan·경고·숫자 아이디·배치 PV·멀티 클러스터 — **완료·실증**(2026-10-26, d3)

배포: 포탈 빌드 d3(소스 7171b28, 이미지 3종 + 잡 이미지 — mpifileutils 3c041e0) → 릴리스(agent → job-image·controller·api) → guard(변경 없음)
→ apply -k(ConfigMap `DMS_ACCOUNT_EMAIL_DOMAIN: example.com`·`DMS_USER_ALLOWED_OPERATIONS: sync,rm`) → rollout restart api·controller.

- **두 번째 클러스터(멀티 클러스터, (i))**: 테스트베드 호스트의 k3s 컨테이너 `dms-k3s2`(v1.31.4+k3s1, API 10.10.10.1:6444 — 테스트베드 IaC 밖,
  격리 세션은 testbed 저장소를 못 고쳐서). 픽스처: namespace lab-x·lab-y, `alice-clusterrole-edit`(lab-x)·`namespaceAdmin`(lab-y,
  junwoo.song@example.com)·`cocoa-song-clusterrole-admin`(lab-y)·대조군(view, 다른 도메인 소유자), StorageClass `c2-nfs`(정적 NFS PV 2개
  → 호스트 `/data/k8s_pv/nfs/c2/<ns>/<pvc>`), DMS 인벤토리 리더(읽기 전용 SA). DMS 등록 `dmsk8s2`(CA·봉인 토큰) → 첫 동기화 10초,
  SC 매핑 `c2-nfs → k8s-pv-nfs` 템플릿 `c2/{namespace}/{pvc}`. 사용자 가시 범위: alice = dmsk8s team-a(edit)·team-b(admin) + dmsk8s2
  lab-x(edit), junwoo.song = team-a(admin) + lab-y(admin — namespaceAdmin), cocoa.song = team-b(edit) + lab-y(admin).
- **(a) namespaceAdmin**: 인식 바인딩 dmsk8s 5 → 8(team-a junwoo.song·team-b alice·team-c ops.lead 소유자), dmsk8s2 2 → 3(junwoo.song),
  다른 도메인(owner@other.org)·view 는 인식 안 됨.
- **(l) 결과 소유 = 목적지 PV 루트 소유자**: alice 클러스터 간 PV→PV sync(dmsk8s team-a datasets → dmsk8s2 lab-x results, dsync) — 확인
  창 단계의 잡에 `dest_pv_owner=1500:1500`, 실행 결과 12항목 전부 1500:1500, setuid 파일 4755 → 755·setgid 파일 2755 → 755, setgid
  디렉터리 2775 유지, 내용 md5 일치. **nsync**(마운트를 갈라 team-a datasets → kraken-kv-cache, PV 루트 1700:1700 으로 잠시) — 12항목
  1700:1700, 같은 비트 처리, 내용 일치. **미리보기 뒤 소유자 변경**(확인 창에서 PV 루트를 1600:1600 으로) → 컨펌 → `pv_owner_changed`
  거부, 목적지 미생성. 요청 상세에 「목적지 PV 소유자 1500:1500(PV 루트 기준)」(실 Chrome).
- **게이트**: alice chown 403 `chown_not_allowed`·chmod 403 `chmod_not_allowed`·scan 403 `scan_unrestricted_only`, 목록 밖 관리자
  ops.k8s scan 403, `/me` 연산 alice·ops.k8s = sync·rm / mason = scan·sync·rm, mason 의 dmsk8s2 lab-y weights scan Succeeded. 숫자
  아이디 가입 인증번호 422 `username_numeric_only`(같은 아이디 재설정은 형식으로 막지 않음 — 404 account_not_found).
- **(c) 동기화 실패는 경고만**: k3s 컨테이너를 멈춰 dmsk8s2 `failing`(Connection refused) — alice 의 lab-x results 는 그대로 선택 가능,
  그 PV 로 보낸 sync Succeeded(막지 않음), 제출 화면 PV 줄에 「목록이 오래됐을 수 있습니다(동기화 실패 중) — … 제출은 그대로 됩니다」(실
  Chrome). 컨테이너 재시작 → 다음 동기화에서 ok.
- **(e) 배치 PV 검색 사이드바**(실 Chrome 1440·375): results 검색 → PV 안 경로 → 「경로 칸에 넣기」 → 1행 `c2/lab-x/results/from-batch`,
  스토리지 `k8s-pv-nfs` 자동, 가로 넘침 0px.
- **PV 탐색 버튼**: ops.k8s(목록 밖 관리자)가 team-c/archive 상세 → 버튼 잠금 + 「이 PV 에 대한 권한이 없습니다 — team-c 에
  ops-k8s-clusterrole-edit|admin 또는 namespaceAdmin(소유자)이 필요합니다」. 콘솔 오류 0.
- **`--context`**: current-context `dms` 인 채 `verify.sh --context dmsk8s`(PASS 4)·`preflight-cluster.sh --context dmsk8s`(PASS 10·WARN 5·FAIL 0).
- 실증 데이터·마운트·PV 루트 소유는 원복. k3s 클러스터는 남겨 둠(제거: `docker rm -f dms-k3s2` + `/data/k8s_pv/nfs/c2` 삭제 + DMS 「k8s 연동」
  에서 dmsk8s2 삭제).

### ✅ PostgreSQL 로 k8s 테스트 실행 + 자리표시자 리터럴 결함 — **완료**(2026-10-26)

- conftest `db`: `DMS_TEST_DB=postgresql` + `DMS_TEST_PG_URL` 이면 테스트마다 새 스키마의 PostgreSQL(기본은 종전 sqlite).
- 일회용 PostgreSQL 16(job tmp, 유닉스 소켓)에서 k8s 테스트 238개 통과 — test_repo_k8s·test_k8s_testbed_fixtures·test_k8s_rbac·test_k8s_sync·
  test_api_k8s·test_k8s_submit·test_pv_owner_chown·test_agent_k8s_worker(+ 기존 PG 비교 test_usage_equivalence).
- 찾은 결함: `db._NAMED`(`:name` → `%(name)s`)가 작은따옴표 리터럴 안의 콜론을 자리표시자로 바꿔 PG 에서만 실패 — 리터럴(`''` 이스케이프 포함)을
  통째로 건너뛰게(`::type` 캐스트는 종전대로). 테스트의 날 SQL 3곳(`'v1:sealed'`·`'v1:AAAA'`·`'sha256:new'`)은 파라미터로.
- 놓친 것: `_NAMED` 에 리터럴 그룹을 더하자 `findall` 이 튜플을 돌려줘 `test_migrations` 의 자리표시자 검사가 기본값 리터럴('mid' 등)을
  위반으로 셌다 — 7171b28 을 push 한 뒤 전체 스위트(3914 통과·2 실패: 이것 + 부하 타이밍 플레이크 `test_request_purger`)에서 드러났고
  16c69c9 가 이름 그룹만 세게 고쳤다.

### ✅ dms-k8s 결정 항목 반영 — namespaceAdmin 맵핑·동기화 실패는 경고만·배치 PV 검색·scan 특권 운영자·숫자 아이디·PV 버튼 — **완료·실증**(2026-10-26, d3)

사용자가 BACKLOG 설계 §10 결정 대기 항목을 정했다(BACKLOG 「설계 §10 결정」).

- **(a) namespace 소유자 맵핑**: `k8s_rbac.namespace_admin_user` — 이름 `namespaceAdmin` + ClusterRole kubeflow-admin + User subject
  하나 + 이메일 도메인 = `account_email_domain` 이면 로컬파트(소문자, 계정 아이디 규칙)가 그 namespace 의 admin. 도메인이 다르거나
  모르면 인식하지 않는다(남의 회사 같은 로컬파트·설정 없는 호출 — fail-closed). 적용 루프·run 개수가 도메인을 넘긴다
  (`apply_run`·`receive`·`snapshot_counts` 의 `email_domain`). 테스트베드 오버레이 `DMS_ACCOUNT_EMAIL_DOMAIN: example.com`
  (테스트베드 바인딩 이메일 — team-c 의 ops.lead 가 새로 보인다). 포탈 문구(인식 안 됨 이유·권한 안내·「또는 namespaceAdmin」).
- **(b)** kubeflow-view 는 권한 아님 — 그대로, 테스트로 고정. **(j)** 「운영자」 = 특권 목록·부여 관리자, **(k)** 사용자 지정 작업은
  그 사용자 목록에 보이지 않음 — 그대로. **(h)** FlashBlade 보류.
- **(c) 동기화가 오래 실패해도 막지 않고 경고만**: `pvLocation.freshnessWarns`(오래됨·지연·실패) → PV 고르기 줄 「목록이 오래됐을 수
  있습니다(동기화 실패 중) — … 제출은 그대로 됩니다」, 스냅숏 기준 줄 경고색. 서버 판정은 마지막 성공 스냅숏 그대로(불변식 12).
- **(e) 배치 생성 「PV 에서 경로 찾기」 사이드바**(k8s 모드): `features/k8s/PvPathPicker` — PV 검색(관리자 PV API, 상한 50) → PV 안
  경로 → 넣을 경로(관리 디렉토리 기준)·실제 경로 표시 → 「경로 칸에 넣기」(scan·rm)·「소스/목적지 칸에 넣기」(sync). 배치 스토리지가
  비면 그 PV 의 스토리지로 채우고, 다르면 잠그고 이유를 보인다, rm 은 PV 루트를 넣지 않는다. 마지막 포커스 칸 → 첫 빈 칸 → 새 행.
  배치 바디는 그대로 직접 경로다.
- **(g) scan 은 특권 운영자만**: `k8s_access.scan_allowed` — 제출(403 `scan_unrestricted_only`, 연산 allowlist 보다 먼저)·/me
  `user_operations`·planner·컨펌(`recheck_request`). 오버레이(testbed-k8s·prod) `DMS_USER_ALLOWED_OPERATIONS: "sync,rm"`. 포탈은 k8s
  모드에서 관리자도 /me 의 연산 목록으로 선택지를 그린다.
- **숫자만으로 된 아이디 거부**: 계정 생성·가입 인증번호 `username_numeric_only`(재설정은 옛 계정용으로 막지 않음), k8s 「사용자 지정」·
  프런트 미러 같은 패턴.
- **PV 탐색 「이 PV로 단일 작업」**: 전체 PV 권한이 없는 관리자의 맵핑 밖 PV 는 버튼 잠금 + 필요한 바인딩 안내(`canUsePv`).
- **ssc DB 정리(운영)**: 제거된 dms-w4·w5 의 agent_nodes 2행·agent_reports 73,865행 삭제(ssc 클러스터 api 파드에서, 읽기 확인 뒤).
- 테스트: k8s_rbac namespaceAdmin 12종·테스트베드 실데이터 소유자 3명·적용 루프 도메인 전달, scan 게이트(사용자·목록 밖 관리자 403,
  특권 운영자 202, DB 변조 행 planner 거부, /me), 게이트 시험의 기본 연산을 rm 으로(`_rm`), 숫자 아이디(저장소·가입·사용자 지정), 프런트
  freshnessWarns·실패 중 경고 + 제출 가능·VolumesPage 3경우·BatchCreate.k8s 6경우.

### ✅ PV 목적지 sync 의 결과 소유 = 목적지 PV 루트 소유자(자동 chown) — **완료·실증**(2026-10-26, d3)

사용자 결정(BACKLOG (l) 「아주아주 중요」): root sync 의 결과를 소스 소유로 두면 다른 namespace 파드(그 PV 의 uid/fsGroup)가 결과를 못
읽거나 못 고친다 → 목적지가 PV 주소면 결과를 **목적지 PV 루트의 uid:gid** 로 맞춘다(dsync·nsync 둘 다 이미 `--chown` 지원).

- 측정: 목적지 쪽 preflight 가 봉쇄(dms_contain) 확인 **뒤** `_PV_OWNER_FN` 으로 PV 루트(링크가 아닌 디렉터리 — 아니면
  `pv_owner_unknown`)의 `stat -c %u:%g` 를 마커 `DMS_PV_OWNER=` 로 찍는다(dsync 단일 파드·nsync 목적지 역할 파드). 술어
  `execution_manifests.pv_owner_measured` 하나(sync ∧ root ∧ contain 의 destination ∧ 명시 chown 없음) — 그 밖의 잡은 스크립트가
  바이트 그대로.
- 기록·전달: stepper `_record_pv_owner` 가 preflight 성공 때 `parse_pv_owner`(마커 **정확히 하나**·숫자 uid:gid ≤ 2^32−2)로 읽어
  `worker_pool.dest_pv_owner` 에 남기고(이벤트 `pv_owner_measured`), `_build_spec` → `JobSpec.result_owner` → `_auto_chown` 이
  미리보기·실행 argv 에 `--chown`. 값이 없거나 틀리면 거부(`pv_owner_unknown`)·빌더 ValueError — 「모름」을 소스 소유 보존으로 접지
  않는다. exec_preflight 가 다시 재서 다르면 `pv_owner_changed`(이벤트에 전·후), 저장값이 없으면(배포 전 미리보기까지 간 잡) 채택.
- 명시 chown: k8s 모드에서 특권 운영자만(제출 403·planner·컨펌 재확인 `chown_not_allowed`) — 일반 사용자가 API 로 규칙을 덮지 못하게.
  특권 운영자의 명시값은 종전처럼 이긴다. PV 소스 → 직접 경로 목적지는 종전(소스 소유 보존).
- 포탈: 소유권 안내(lib/syncOwnership `pvDestination`·`pvOwner`) 「목적지 PV 루트의 소유자·그룹(점검 뒤 확정)」 → 잰 뒤 「목적지 PV
  소유자 1000:100(PV 루트 기준)」, 요약 실행 권한 `PRIV_ROOT_SYNC_PV`, 「목적지가 이미 있는 경우」 문장, 요청 상세의 목적지 소유.
- 테스트: `tests/test_pv_owner_chown.py`(술어·argv·형식 거부 10종·파서·실 sh 측정(소유 = os.stat, nsync 역할별, 봉쇄가 먼저, 루트가
  파일·링크·없음이면 거부)·stepper 흐름(기록→미리보기 spec, 모름 3종 거부, exec 같음·바뀜·채택, 규칙 밖 잡은 로그를 읽지 않음)),
  k8s 제출 chown 403·planner·컨펌 변조 행, 마커 화이트리스트 그물에 PV 목적지 spec, 프런트 syncOwnership·requestSpec·SubmitJob.k8s.
- **리뷰 반영(자동 보안 리뷰 HIGH + 적대적 리뷰)**:
  - **setuid/setgid(높음)**: 두 도구는 chown 뒤 권한을 복사해 커널이 지운 비트를 되살린다 — PV 루트가 0:0 이면 사용자의 setuid
    파일이 **setuid-root** 가 됐다(로컬 재현: 2a10a18 `--chown 0:0` → 4755 0:0). mpifileutils `3c041e0` 「--chown clears setuid/
    setgid on regular files」(chown(2) 규칙, 디렉터리 setgid 유지, --chown 없으면 종전 보존) → 4755·2755 가 755 로, 2775 디렉터리 유지,
    재-sync 도 지움(dsync·nsync 6/6, 기존 시나리오 전부 그대로). REF → 3c041e0, 고정값 테스트에 2a10a18 금지.
  - chmod 도 k8s 모드에서 특권 운영자만(`chmod_not_allowed` — 제출·planner·컨펌), 포탈의 chown·chmod 칸은 특권 운영자에게만 열린다
    (고급 옵션의 direct·quiet·batch_files 는 관리자 그대로).
  - 마커를 **못 읽은** 것(apiserver 순간 오류 — read_log 의 log None)은 거부하지 않고 다음 틱에 다시 읽는다(상한 20틱), 읽었는데 마커가
    없거나 여럿일 때만 `pv_owner_unknown`.
  - 잡 행의 chown·chmod 만 변조(무검증 INSERT)되면 `_build_spec` 이 요청 행과 비교해 끊는다(`chown_not_allowed`, 이벤트
    `chown_mismatch_at_step`).
  - 포탈 chown 칸 도움말이 PV 목적지에도 「소스 소유 보존」이던 것, 「결과를 고칠 수 있게」 과장 문구 정정. stepper spec → 실제 argv
    `--chown` 통합 테스트 추가.

> 같은 날 기본값이 바뀌었다: PV 목적지 결과 소유 기본 = **목적지 스토리지 기본 소유자**(uid:gid), 이 항목의 PV 루트 측정은 특권 운영자
> 옵션 `pv_owner: "pv_root"`·규칙 이전 옛 행에만 남는다 — 위 「스토리지 기본 소유자(uid:gid) — PV 목적지 sync 결과 소유」.

### ✅ nsync 목적지 링크 추종 수정 반영 — root nsync 의 PV 목적지 허용 — **완료·실증**(2026-10-26, d1 → d2)

k3 2차 보안 리뷰가 찾은 nsync(dms-mpifileutils) 목적지 쪽 링크 추종을 도구에서 고치고, 그동안 DMS 가 막아 둔 가드를 풀었다.

- 도구(chahwansong/mpifileutils main): `5958e4e` 「nsync: never follow symlinks below the destination root」 — 목적지 루트를 O_PATH
  dir fd 로 한 번 열고 상위 디렉터리를 성분별 `openat(O_PATH|O_DIRECTORY|O_NOFOLLOW)` 로 걷는다(없으면 `mkdirat`, EEXIST 면 다시
  O_NOFOLLOW 로 열어 디렉터리일 때만 통과). 파일 생성은 `openat(O_NOFOLLOW|O_CREAT|O_TRUNC)`, 삭제는 `unlinkat`/재귀 `unlinkat`(링크는
  링크 자체만), 심링크 생성은 `symlinkat`. `8cd72f3` 「close the destination metadata symlink-follow TOCTOU」(자동 보안 리뷰) — chown·
  utimes 는 늘 `AT_SYMLINK_NOFOLLOW`, chmod 는 `O_PATH|O_NOFOLLOW` + fstat(링크 거부) + `/proc/self/fd/N`. 링크 성분을 만나면 그
  액션만 실패한다(쓰지 않는다). 로컬 실증(Dockerfile 레시피 그대로 빌드): 링크 너머 유출 0(옛 바이너리 `--batch-files 1` 200개 중
  130개), 다중 rank 2000×5 회 0, 기능·경계·보안·메타데이터 시나리오 전부 통과, 옛 바이너리 대비 기능 회귀 없음.
- **d1 라이브 실증에서 찾은 도구 결함 → `2a10a18`**: 8cd72f3 은 목적지 루트를 O_PATH 로 먼저 여는데, 목적지가 아직 없으면(새 폴더로
  sync — 가장 흔한 경우) ENOENT 로 모든 액션이 실패했다(「Cannot open destination root」, 실행 rc=1·0 바이트). 루트 자신의
  `MKDIR "."` 는 건너뛰고, 예전에 첫 MKDIR·COPY 가 루트를 만들던 경로 기반 `mkdir -p` 를 지웠기 때문이다. 로컬 시나리오가 전부 목적지를
  미리 만들어 놓쳤다. `2a10a18` 「create a missing destination root before walking below it」: 실행 단계가 루트를 못 열면(ENOENT) 루트와
  없는 상위를 `mkdir -p`(EEXIST 허용 — 목적지 rank 동시 생성 안전)로 만들고 다시 연다 — 루트·그 위는 호출자가 준 대상 경로라 원본과
  같은 신뢰 모델, 루트 **아래**만 O_NOFOLLOW. dry-run 은 실행 단계에 오지 않아 미리보기는 목적지를 만들지 않는다. 새 시나리오(func3 —
  새 루트·상위까지 없음·목적지 rank 3 + 작은 배치·dry-run·빈 소스·루트 자리 파일·루트 링크·재실행): 1b93d54 통과 / 8cd72f3 4개 실패 /
  2a10a18 통과(빈 소스 → 없는 루트는 1b93d54 rc=1 → 2a10a18 rc=0 으로 오히려 나아짐), 기존 링크 탈출·기능 시나리오 그대로 통과.
- DMS: `Dockerfile.mpifileutils` `MPIFILEUTILS_REF` 1b93d54 → `8cd72f3`(d1) → `2a10a18`(d2)(nsync.c 만 바뀜 — dscan·dsync·drm 동일). planner 의
  `k8s_access.check_nsync_destination`(5a') 제거 — 두 스토리지를 함께 마운트한 노드가 없으면 PV 목적지 sync 도 nsync 로 계획된다.
  사유 코드 `pv_sync_needs_shared_node` 는 더 이상 내지 않고 목록·문구에만 남긴다(옛 거부 행 표시 — 「지금은 허용, 다시 제출」).
- 테스트: 거부 테스트를 「계획됨·nsync」로 교체, 잡 이미지 고정값이 옛 커밋(faefdaf·1b93d54 — 링크 추종, 8cd72f3 — 새 목적지
  실패)이 아님을 고정(가드 없는 planner 와 도구 고정값이 짝이라는 계약).
- **라이브 실증(dmsk8s, d2)** — 두 PV 를 함께 마운트한 노드가 없게 k8s-w2 의 `/k8s_pv/nfs`·k8s-w1 의 `/k8s_pv/onefs` 를 잠시 내리고
  (fstab 그대로, 끝나고 원복 — 두 노드 `lookupcache=positive` 유지, 에이전트 보고 Ready 확인), alice(일반 사용자) PV 주소로:
  - 회귀(마운트 분리 전): team-a datasets → kraken-kv-cache **dsync** Succeeded 146개, 결과 트리 = 원본(모드·크기·md5·링크 145항목).
  - **nsync 기능**: 같은 쌍 → 새 목적지 Succeeded 146개·327,184 B, 결과 트리 = 원본(145항목). d1(8cd72f3)에서는 같은 요청이 실행
    rc=1·0 B(「Cannot open destination root」) — 위 2a10a18 의 발견 경위.
  - **nsync 링크 공격**: 목적지 PV 안에 같은 onefs 스토리지의 남의 영역을 가리키는 `sub`(디렉터리)·`file.txt`(파일)·`a/b`(깊은
    상위) 링크를 심고 같은 이름의 소스를 sync → Succeeded 210개, 남의 영역 **변화 0**(파일 2개·내용·모드 그대로, 유출 0), 목적지의
    세 링크는 실제 디렉터리·파일로 바뀜(sub/ 200개, file.txt = 소스 내용, a/b/c/f.txt).
  - **비 PV root nsync**: mason(특권 운영자) 직접 경로 cephfs-third(w1) → cephfs-secondary(w2), 같은 링크 공격 → Succeeded, 유출 0.
  - **증분**: 소스 파일 하나 변경 + 하나 추가 후 같은 목적지로 다시 → 3건·3,006 B 만 옮기고 결과 트리 = 원본(146항목).
  - **도구 대조**(파드에서 잡 이미지 nsync 를 직접, onefs NFS, `--batch-files 1` 링크 공격): k3 이미지 130/200 이 남의 영역에 써지고
    rc=0(조용히 샘) → d1·d2 이미지 0/200, rc=1(그 경우 아무것도 쓰지 않고 멈춤). 기본 배치는 링크를 실제 디렉터리로 바꿔 200개 복사.

### ✅ dms-k8s RBAC·PV — 인벤토리·관리자 화면·제출·권한·스토리지·PV 봉쇄 — **완료·실증**(2026-10-25~26, k3)

사용자 지시(「DMS-k8s 개발」 세션, 2026-10-25): dms-k8s 는 LDAP 대신 **k8s RoleBinding·PVC 맵핑**으로 권한을 판정하고 **모든 잡을
root** 로 돌린다. `rolebindings -A` 의 `<user>-clusterrole-admin|edit`(ClusterRole `kubeflow-admin|edit`) → 그 namespace 의 모든
PVC 읽기·쓰기(ID = 이름 앞부분의 `-` → `.`), 사용자는 PV 이름(= PVC NAME 열)을 고르고 경로는 VOLUME 열로 만든다(GPFS
`{volume}/{volume}-data`, OneFS `data/csi/{volume}`, 테스트베드 NFS + csi-driver-nfs). 여러 클러스터의 API 서버·토큰 등록, 호출 노드
primary·secondary, 동기화 상태 UI. 설계 `docs/history/specs/2026-10-25-dms-k8s-rbac-pv-design.md`(v2 — 적대적 검토 3관점 반영).

- **테스트베드(슬라이스 0, testbed 저장소 7729b50)**: 호스트 export `/data/k8s_pv` → 워커 `/k8s_pv/{nfs,gpfs,onefs}`, csi-driver-nfs
  v4.13.5, SC tb-nfs·tb-gpfs·tb-onefs(정적 PV `k8s-3fa9c2d71e`), ns team-a·b·c 와 인식 바인딩 5 + 대조군, 읽기 전용 리더 SA·장기 토큰.
  실데이터 목록 JSON 을 `tests/fixtures/k8s/testbed-*.json` 으로(토큰 없음). validate PASS 219·post-boot-check PASS 89.
- **인벤토리 백엔드(슬라이스 1, c2655ae)**: 마이그레이션(§3 — k8s_* 8표, `storages.kind`, `requests.target_label`), `k8s_rbac`(D5
  맵핑 규칙)·`k8s_sync`(D16 배정 — 정규 run 은 primary, 90초 미 claim 이면 secondary, 노드성 실패는 30초 뒤 secondary 1회 대체)·
  `pv_paths`(D9 템플릿 — `{volume}`·`{pvc}`·`{namespace}`·`{storageclass}`·`{subdir}`·`{attr:키}`·`{handle:구분자:i[+]}`, 우선순위 SC
  덮어쓰기 > backend 프리셋), `repositories/k8s`, 에이전트 `k8s_fetch` 워커 스레드(httpx, CA·SNI·검증 안 함, 410 재시작, 크기 상한),
  `routes_agent` claim·결과, `routes_k8s`(관리 §5 + 사용자 볼륨), 컨트롤러 `k8s-inventory` 루프(diff 적용·CAS·expired·정리)·보존,
  봉인 용도 `k8s_cluster_token`, 컨트롤러 리스 holder 에 HOSTNAME. 보안 리뷰 반영(ce2a9ef): 클러스터 토큰은 검증된 그 서버에 묶는다
  (TLS 서버 이름 변경·검증 끄기도 토큰 재입력).
- **관리자 화면(1482542)**: 「k8s 연동」 5탭(개요·StorageClass·PV 탐색·사용자 권한·동기화 이력), 클러스터 등록 Dialog(리더 SA·토큰
  Secret YAML 복사).
- **제출·권한·스토리지(슬라이스 2)**:
  - **k8s 신원 모드**(D1) `DMS_IDENTITY_MODE ∈ {ldap, k8s}`(코드 기본 ldap) — k8s 인데 `DMS_LDAP_URI` 가 있거나 특권 허용이 false 면
    기동 거부. **root 강제**(D2): 단건 제출에 서버가 `run_as_root: true` 를 싣고 명시 false 는 422 `run_as_root_required`. **root 허용
    술어 하나**(D3) `identity.root_allowed` — k8s = 세션 인증, ldap = 종전 privilege_eligible — 를 제출·`resolve_job_identity`
    (REQUESTED)·`confirm_job` 세 지점이 쓴다(일반 사용자 sync·rm 이 컨펌에서 409 로 끊기던 결합 — 리뷰). 배치 자식(IF_ELIGIBLE)은 그대로.
    k8s 모드는 그룹 denylist 를 평가하지 않고(groups=[]) 그룹 규칙 추가는 422 `denylist_group_unsupported`. 공유 토큰 제출은 403
    `privileged_not_authorized`.
  - **맵핑 면제**(D4) = `can_run_as_root`(관리자 + 특권 목록/부여 + 세션, 의미 불변) — 면제자는 직접 경로·전체 PV·PV ↔ 직접 경로 혼합,
    그 밖은 PV 주소만(403 `raw_path_not_allowed`) + 요청자 ID 의 맵핑(403 `k8s_access_denied`). 요청 행 기반 동등 술어
    `k8s_access.unrestricted_request`(planner·컨펌). `/api/auth/me` 에 `identity_mode`·`unrestricted`·`user_operations`.
  - **PV 주소 제출**(§6): 바디 `pv`·`source_pv`·`destination_pv` + 하위 경로. 게이트 순서 유지보수 → 연산 → 클러스터 동기화(409
    `k8s_mapping_unknown`) → PV 해석(422 `pv_not_found`·`pv_not_bound`·`pv_storageclass_unmapped`·`pv_path_unresolved`·
    `pv_root_rm_forbidden`·`pv_and_storage_conflict`) → 해석된 스토리지로 `storage_admin_only` → 맵핑·raw → sync 쌍 → owner·root →
    `build_data_payload` + pv 블록 `{cluster, namespace, pvc, volume, storage_class, root, path}` + `requests.target_label`(「PV 이름 —
    ns (sc) / 하위경로」, sync 는 「 → 」). PV 루트 rm 은 **정규화한 최종 target == 루트**로 판정(D12 — `''`·`'.'`·`'./'`).
  - **재확인**(D13) `k8s_access.recheck_request` — planner 3a·컨펌이 같은 함수: `pv_moved`·`pv_not_found`·`pv_target_outside`·
    `raw_path_not_allowed`(pv 블록 없는 비면제 행)·`k8s_access_denied`(권한 회수), 컨펌은 종단 + 409. **sync 쌍 면제**(D14)
    `k8s_access.pair_gate_exempt`(k8s 모드 ∧ 양쪽 PV) — 제출·planner 3b·컨펌.
  - 작업 목록 경로 검색에 `target_label` OR(PV 이름·namespace·StorageClass 로 걸린다). k8s 모드 owner_username = 계정 아이디 규칙.
    사용자 스캔 경로 등록·통계는 k8s 모드에서 면제자 전용(422 `scan_path_unrestricted_only`).
  - **스토리지**(D10·D11): `kind`(filesystem·pv·both, 422 `invalid_storage_kind`)를 등록·수정·목록·사용자 목록에, 매핑된 스토리지의
    filesystem 전환 422 `storage_has_sc_mappings`, 스토리지 삭제 시 같은 트랜잭션에서 SC 매핑 정리 + 감사. 백엔드 라벨 `onefs`·`nfs`
    추가(backend_type 은 PV 경로 프리셋 키이기도 하다).
  - **배포**: testbed-k8s 오버레이에 `DMS_IDENTITY_MODE: "k8s"`·`DMS_USER_ALLOWED_OPERATIONS: "sync,scan,rm"`. dms-k8s 의
    `overlays/prod` 를 무 LDAP 으로(LDAP 키 null·`patch-agent.yaml`·render 필수 키에서 LDAP 제거·install 시크릿 4키 —
    `tests/test_prod_k8s_overlay.py`; BACKLOG 「overlays/prod 의 LDAP 필수」 해소).
  - 사유 코드 14개 양쪽 등록(슬라이스 1 이 남긴 `pv_not_bound`·`pv_storageclass_unmapped`·`pv_path_unresolved` 포함).
  - 테스트: `tests/test_k8s_submit.py`(테스트베드 실데이터 위 수용 — 일반 사용자 PV→PV sync 제출 → 계획 → 미리보기 대기 → 컨펌,
    게이트·재확인·면제·me·검색), `test_storage_kind.py`, `test_prod_k8s_overlay.py`, 기존 테스트 갱신(백엔드 8종·me 필드·사용자
    스토리지 필드).
- **포탈 제출 화면(슬라이스 3, ceaebcc)**: 단일 작업의 k8s 모드 — `LocationField`(쪽마다 「PV 주소 | 직접 경로」, 비면제는 PV 주소만,
  PV 고르기는 내 권한 PV 목록 `usePvCatalog`·하위 경로·실제 경로 미리보기), 스토리지 화면의 「종류」, 작업 목록·상세의 PV 표기
  (`pathParts`), 계정 라벨·`?create=` 바로가기. vitest 1519.
- **PV 주소 봉쇄(2026-10-26 보안 리뷰 HIGH)**: 모든 잡이 root 라 커널 DAC 가 없어, 사용자 파드가 자기 PV 안에 남의 PV 를 가리키는
  심링크를 심으면 PV A 권한만으로 root dsync·dscan 이 PV B 를 읽거나 덮어쓸 수 있었다(재확인은 글자로 「경로 ⊆ PV 루트」만 본다).
  stepper 가 PV 쪽의 {PV 루트, 기준 managed_root} 를 `paths["contain"]` 로 싣고, preflight(sync·nsync 역할별·scan)가 `dms_contain`
  으로 「PV 루트 안(글자) + managed_root 부터 심링크 성분 없음 — 마지막 성분 포함」을, 러너가 실행 직전에 같은 규칙(`dms_job_runner.
  contain.pv_path_redirected`, env `DMS_JR_CONTAIN`)을 본다 — dsync·dscan 은 런처가, nsync 는 **각 rank 가 도구 exec 직전에 자기 역할
  쪽만**(rank.sh 첫 줄, 런처가 mpirun 출력의 마커를 파드 로그로 끌어올린다). PV 주소 rm 은 기존 rm 봉쇄(기준 managed_root) + PV 루트
  글자 경계. 2차 보안 리뷰(같은 날, 9e666b1 대상) 반영: 처음엔 PV 루트를 realpath 기준점으로 써서 PV 루트 자신·그 위 성분이 링크면
  통과했고(PV rm 도 기준을 PV 루트로 좁혀 같은 후퇴), nsync 는 실행 시점 재확인이 없었다(Volcano 대기 동안의 창). 사유
  `target_path_redirected`(문구 일반화). 블록 root 가 `..` 로 managed_root 밖이 되는 변조 행은 빈 루트로 막는다. PV 가 아닌 잡의
  매니페스트·rank.sh 는 바이트 단위로 그대로. `tests/test_pv_containment_sh.py`(실 sh·러너·rank.sh 를 같은 트리로 대조)·
  `test_stepper_enrich`(stepper 가 싣는 값).
- **정확성·사용성 리뷰(2026-10-26, 5관점 + 관점별 반박 검증 — 실결함 12건 반영, 기각 5건은 그대로)**:
  - 동기화 이력이 SC·PV 목록을 못 받은 run(`changes` 값 null)에서 화면 전체가 렌더 예외로 죽던 것(high) — null 항목은 「이번에 받지
    못함 — 이전 값 유지」, 내용만 바뀐 항목(`changed` — PVC Pending→Bound·재생성, 바인딩 subjects)을 「~N」·「바뀜」 목록으로.
  - run 경고 `{phase, reason, http_status}` 를 「StorageClass 목록: <사유 문구>」로(프런트가 옛 `list` 키만 읽어 원문 노출).
  - RoleBinding 하나의 subjects 가 64개를 넘으면 스냅숏 전체가 거부돼 동기화가 멈추던 것 — 상한 4096, 넘는 몫은 잘라 낸다.
  - 사용자 권한 화면의 근거 바인딩 역할·subject 가 서버 모양(grant 수준 `role_name`·`subjects`)과 달라 표시되지 않던 것.
  - 클러스터 삭제가 진행 중 run 을 닫고(재등록한 같은 이름이 옛 run 결과·실패를 물려받던 것), 재등록이 남은 스냅숏 행을 지운다.
  - SC·PV 목록을 한 번도 받지 못한 클러스터의 개수를 0 이 아니라 null(「목록 권한 없음·모름」).
  - PV 고르기가 매핑 스토리지의 비활성·관리자 전용을 반영(`storage_disabled`·`storage_admin_only` — 직접 경로 피커와 같은 술어).
  - 면제 관리자의 고른 PV 재해석을 이름 정확 일치(`pvc=`)로 — 부분 일치 + 상한 50 에 잘려 「찾을 수 없음」으로 제출이 잠기던 것.
  - 경로 템플릿 검증이 `{namespace}.{pvc}` 처럼 자리표시자 사이의 `.` 를 성분으로 오인해 거부하던 것(렌더 성분으로 판정).
  - 클러스터 등록 창: 토큰 필수(서버 계약과 같게), StorageClass 예시 경로의 사유를 한국어 문구로(영문 진단은 보조 줄).
- **2차 리뷰(같은 날, 07d3695..4c2f529 — 3관점 + 반박 검증, 실결함 5건 반영·기각 3건)**:
  - **nsync 도구의 트리 안쪽 링크 추종**(high): nsync 목적지 쪽은 상위 디렉터리를 stat 로 확인하고 파일을 O_NOFOLLOW 없이 연다 —
    목적지 PV 안에 사용자가 심은 `sub → 남의 PV` 링크 너머에 root 로 써졌다(실 이미지 재현, `--batch-files 1` 에서 200개 중 128개).
    root 잡의 PV 주소 목적지는 nsync 로 계획하지 않는다(planner — `k8s_access.check_nsync_destination`, 새 사유
    `pv_sync_needs_shared_node`; 두 스토리지를 함께 마운트한 노드의 dsync 만). 도구 수정(dms-mpifileutils)은 BACKLOG.
  - 런처의 nsync 마커 끌어올리기를 줄 전체 일치 + mpirun 실패일 때만(경로에 마커 글자가 든 정상 nsync 를 오인하던 것).
  - `delete_cluster` 의 잠금 순서를 run → 클러스터로(apply_run·expire_overdue 와 반대라 PG 교착 — 실 PG 재현).
  - 면제 관리자의 PV 고르기 경로(관리자 볼륨 API)에도 비활성 스토리지 표시.
- **push 보안 리뷰 후속(k4)**: k8s 모드의 신원 = 「계정 아이디 == RoleBinding ID」라 인증 메일이 stub·꺼짐이면 남의 ID 선점이
  가능 — 개요 응답 `signup_guard`(mail·stub·open) + 화면 경고(기동은 막지 않는다 — 테스트베드는 stub). 에이전트 결과 본문을 스트리밍
  카운트로 받아 상한에서 끊는다(chunked 본문을 다 버퍼링한 뒤 재던 것). 에이전트 공유 토큰의 노드 행세(위조 인벤토리 포함)는 BACKLOG.
- **실증(k3 = 소스 3e9aae6, 테스트베드 dmsk8s, 2026-10-26)** — 빌드 210초, 릴리스 agent → api(migrate) → controller + 잡 이미지,
  오버레이 c0ae3ca, `apply -k` 뒤 api·controller `rollout restart` 로 `identity_mode: k8s`(ConfigMap 만 바뀐 apply 는 재시작하지
  않는다 — 런북 13-8). 등록(런북 13-9): 스토리지 3종 + cephfs 종류, 호출 노드 k8s-w1/w2 5분, 클러스터 `https://k8s-cp1:6443`(CA ·
  봉인 토큰) 첫 동기화 ~30초 — SC 3 · PV 6 · PVC 7(Bound 6) · 바인딩 36(인식 5) · 사용자 4, 지문 = kube-system uid. SC 매핑 3개의
  예시 경로 6개가 워커 실디렉터리와 일치(NFS subDir · GPFS `{vol}/{vol}-data` · OneFS `data/csi/{vol}`).
  - **역할**: alice = team-a edit + team-b admin, junwoo.song = team-a admin, cocoa.song = team-b edit, 목록 밖 관리자 ops.k8s = 0개,
    특권 관리자 mason = 면제 전체 7개. 거부: 직접 경로 403 `raw_path_not_allowed`, 권한 밖 PV 403 `k8s_access_denied`, 명시 비 root
    422, Pending PVC 422 `pv_not_bound`, `..` 422 `unsafe_path`, PV 루트 rm(`''`·`.`·`./`) 422 `pv_root_rm_forbidden`.
  - **세 벤더 scan**(root, PV 주소): 마커 4파일 — 63·67·71 바이트가 정확히 나왔다(봉쇄 env `DMS_JR_CONTAIN` = [쪽, 경로, PV 루트,
    managed_root] 실림).
  - **심링크 봉쇄**: alice PV 안 `escape → ../../team-c/archive` 로 scan 2·sync 소스·sync 목적지 부모·rm 상위 다섯 시도 모두 preflight
    `target_path_redirected`. 미리보기 뒤 컨펌 전 바꿔치기 → exec_preflight 가 Rejected. 실행 vcjob 이 뜬 1초 뒤 바꿔치기 → 런처 6d
    (`DMS_JR_PV_PATH_REDIRECTED side='source'`) 가 mpirun 전에 Failed. team-c 데이터 무손상·유출 0. 링크 자체 rm 은 링크만 지웠다.
  - **sync**: PV→PV(nfs→nfs, gpfs→gpfs) 컨펌·실행 성공, cephfs→PV·PV→cephfs(면제 관리자 혼합) 성공. 첫 nfs 다중 노드 dsync 가 다른
    노드 NFS 클라이언트의 음성 dentry 캐시로 ENOENT 실패 → 테스트베드 PV 마운트에 `lookupcache=positive`(testbed group_vars, 런북 전제).
  - **동기화 장애·대체**: 잘못된 토큰 = `k8s_sync_unauthorized`(401), TLS 서버 이름 불일치 = `k8s_sync_tls_failed`(원문 표시), 같은
    클러스터 IP 이중 등록 = 「같은 클러스터로 보입니다」. 주 노드 에이전트 SIGSTOP → 요청 93초 뒤 보조 k8s-w2 가 가져가 적용.
  - **권한 회수**: cocoa.song 의 sync 를 컨펌 대기로 둔 채 RoleBinding 삭제 → 동기화 13초 → 컨펌 409 `k8s_access_denied`(Rejected),
    새 제출 403. 복구 → 12초 뒤 권한 복귀.
  - **화면(실 Chrome, 1440·375)**: 5탭·상세 창·alice 단일 작업 PV 고르기(직접 경로 토글 없음, 실제 경로 미리보기)·작업 목록 PV 표기 —
    콘솔 오류 0, 가로 넘침 0.

### ✅ 전체 작업: 요청자 부분 일치 + 배치 이름·대상 경로 검색 + 열 폭(요청 ↓ · 배치 ↑ · 시각 = 글자 폭) — **완료·실증**(2026-10-25, k2)

사용자 요청(「DMS-k8s 개발」 세션): 「포탈의 전체 작업에서 지금 요청자 아이디로만 필터를 걸수 있는데 배치 이름, 대상 (경로)
등으로도 검색 및 필터링 할수 있게 해줘. 일부 단어라도 포함하거나 매치되면 그것도 보여주도록 해줘 (exact match 완화). 그리고
배치 column 이 너무 좁은데 요청ID 칸 줄이고 생성/갱신 칸을 텍스트에 맞게 조절해서 배치 칸을 조금 키워줘.」

- **API**(`GET /api/user/requests`): `requester`(관리자 — 정확 일치 → 부분 일치, 결과는 예전의 상위집합이라 하위 호환)·
  `batch_name`(관리자 — 배치 이름 부분 일치, 또는 6자 이상 hex 면 배치 id 앞부분: 이름 없는 배치·「기록 없음」 묶음은 화면에
  id 앞 12자로 보인다)·`path`(누구나 — 화면 「대상」 표기). 셋 다 대소문자 무시, 공백으로 나눈 단어는 모두 들어 있어야
  한다(AND), 「→」(sync 표기 붙여 넣기)·NUL 은 구분자, 칸당 단어 8개·512자. 비관리자에겐 requester·batch_name 을 무시한다.
- **저장소**(`RequestsRepository.list`): 격리 = `requester_id` 정확 일치 그대로, 검색은 별도 kwarg(`requester_like`·
  `batch_like`·`path_like`)로 AND 만 — 부분 일치를 격리 kwarg 에 넣으면 'al' 이 'alice' 를 본다(ARCHITECTURE 불변식 추가).
  대상 검색은 resource_key 원문이 아니라 `data.<op>:` 접두·scan 옵션 지문을 뗀 식(`_TARGET_TEXT_SQL`)에 건다 — 원문이면
  「data」·「scan」이 그 연산 전체에, hex 짧은 검색어가 무관한 scan 에 걸렸다(32만 행 실측). 컬럼·패턴 양쪽을 **같은** SQL
  `lower()` 로(`lower(col) LIKE lower(:p)`) — LIKE 만 쓰면 PG 는 대소문자를 구분하고 sqlite(테스트)는 무시해 테스트베드에서만
  결과가 달라진다. 배치 이름 조건은 `COALESCE(batch_id IN (…), FALSE)` — 맨 IN 이면 PG 가 semi-join 중첩 루프를 골랐다. LIKE 이스케이프는
  `repositories/text_search.py` 한 벌(사용량 분석 검색도 이걸 쓴다). 스키마 변경 없음.
- **포탈**(`JobsList`): 검색칸 셋(요청자·배치 이름 = 관리자, 대상 경로 = 누구나, `type=search`·고정 폭 `w-40`·placeholder
  「…(일부)」). 타이핑이 멈춘 뒤 300ms 에 한 번 묻고(예전 요청자 칸은 글자마다 물었다) 한글 조합 중에는 끝의 미완성 자모만 떼고
  묻는다(`searchField.ts` — 「이ㄱ」 → 「이」, 마지막 음절이 조합 중인 「이관」 도 멈추면 적용). 적용된 검색어가 바뀌면 선택을 비운다(안 보이는 행을 지우지 않는다 — select 필터와 같은 원칙).
  검색어가 있는 동안 폴링 3초 → 10초(부분 일치는 드문 검색어면 범위 전체를 훑는다 — 단일 DB 커넥션).
- **열 폭**: 관리자 요청 id 링크 6.5 → 5rem(앞 10자 + 말줄임 — 전체 id 는 title·DOM), 요청·생성·갱신 머리칸 `w-px`(열이 내용
  폭에 붙는다 — 1920 에서 시각 칸이 170·161px 로 벌어지던 것이 130px 글자 폭으로), 배치 칸 6 → 7.5rem(머리칸
  `calc(8rem + 1px)` = 129px). 실 Chrome 실측(모의 행): 1280 에서 배치 105 → 129px, 요청 106 → 82px, 대상 181px 그대로·래퍼
  넘침 없음. 배치 이름 두 줄 분할 폭 11/10 → 13/12(7.5rem 기준 — 「성장 모니터링」 은 이제 한 줄).
- 테스트: 백엔드 `tests/test_api_request_search.py`(9 — al/alice 격리 그물, 비 ASCII 대문자 화면 표기, `_`·`%`·`\` 리터럴, 접두·지문 오탐 없음, 한글,
  배치 이름·id 앞부분·기록 없음, 커서 조합, 513자 422·NUL, SQL 의 lower() 고정). 프런트 vitest(검색칸 노출·쿼리·디바운스·
  IME·선택 비우기·폭 클래스), e2e E13(실 sqlite 서버 — 세 칸 부분 일치·디바운스), E7 배치 머리칸 129px.
- 실증(k2, dmsk8s — 포탈 빌드 a48ba59c, 소스 dms-k8s 체크아웃 1b0febe, 이미지 3종 210s, 릴리스 agent → api → controller + 잡
  이미지 Applied, 오버레이 bump 뒤 guard 0·diff 0·LDAP 키 0): 시드 = root scan 3 + 이름 배치 「k2 검색실증 아카이브 이관(1차)」(2항목).
  API(PG): path=GROWTH → e2e/growth 2건, batch_name=「이관 검색실증」(순서 바꿈) → 배치 자식 2건, batch_name=E5FE5A35(대문자 id
  앞부분) → 2건, requester=MAS → 5건, path=data.scan → 0건(접두 오탐 없음), path=cephfs-third → 1건. 실 Chrome(1440): 같은 값을
  검색칸에 40ms 간격으로 쳐도 서버 요청은 검색어당 1회(GR·GRO… 없음), 보이는 행 동일, 「zz-nobody」 → 빈 결과 문구, 콘솔 오류 0.
  열 폭(같은 데이터, k1 → k2): 1280 요청 107 → 82·배치 105 → 129·생성 140 → 139·갱신 133 → 132px, 1920 요청 192 → 82·생성
  252 → 139·갱신 239 → 132·대상 396 → 536px, 모든 폭에서 표 래퍼 넘침 없음.
- 적대적 리뷰(4관점 × 반박 검증, 지적 8 중 확인 5) 반영: ① 패턴을 파이썬 `str.lower()` 로 낮춰 DB `lower()` 와 달라 화면 표기
  「Ärger」·「ΟΔΟΣ」 를 그대로 쳐도 자기 행이 안 나옴(sqlite·C 로캘 PG 재현) → 양쪽 SQL lower. ② 한글 IME 는 마지막 음절을
  Space·blur 전까지 조합 상태로 둬 「이관」 을 치고 멈춰도 목록이 그대로(실 Chrome 재현) → 조합 중에도 적용(자모만 뗌). ③ 배치
  이름 IN 이 PG16 32만 행·일치 배치 300개에서 2.75s(중첩 루프) → COALESCE 로 hashed SubPlan. ④ 키보드 「필터 해제」 뒤 포커스가
  검색칸 셋 앞 상태 필터로 → 칩 바로 앞 대상 경로 칸. ⑤ E13 두 단언이 디바운스 창 안에서 직전 결과로 통과 → 응답 대기 후 단언.

### ✅ nsync 미리보기·실행 파드가 반대편 스토리지를 마운트하던 문제 — **완료·실증**(2026-10-22, k1 · ssc d185)

k8s 테스트베드 실증 중 발견: nsync 미리보기 vcjob 의 destination-worker(k8s-w2)가 `hostPath type check failed: /cephfs-third
is not a directory` 로 ContainerCreating 에 묶였다(사용자 취소로 정리). `_build_nsync_job` 이 런처·source-worker·
destination-worker **모두에** 소스와 목적지 스토리지를 hostPath(type Directory)로 붙였다 — nsync 는 정의상 두 스토리지를 함께
마운트한 노드가 없을 때 고르는 도구라 반대편 경로는 그 노드에 없다. preflight 는 같은 이유로 이미 역할별 마운트였다(코드 안의
불일치). 옛 ssc 테스트베드는 반대편 노드에 남은 **빈 마운트포인트 디렉터리**(dms-w1·w2 의 8월자 빈 `/cephfs-secondary` 등)가
이걸 가렸다 — 테스트베드 분리로 dms-w3 의 `/cephfs-third` 가 사라져 ssc 도 같은 상태가 됐다(d185 로 복구, 아래).

- `execution_volcano._volumes(role=…)` 하나가 정한다: nsync vcjob 은 런처 = 아티팩트 base 만(`role="launcher"`), source-worker =
  소스 스토리지, destination-worker = 목적지 스토리지(+ 각 아티팩트 base). `build_volcano_job(role_volumes=…)` — nsync 에 없으면
  ValueError(→ submit_failed, 예전 모양으로 조용히 돌아가지 않는다). primary(dsync·scan·rm)는 그대로.
- 근거: 러너가 nsync 에 `--role-mode map` 을 싣는다 — 고정 커밋 nsync.c 를 읽어 map 모드에선 rank 가 자기 역할 경로만 만진다는
  것을 확인(auto 모드는 역할 판정에서 모든 rank 가 양쪽을 `access()`). 런처는 nsync 에서 데이터 경로를 만지지 않는다(런처의
  데이터 접근은 drm 봉쇄 재확인뿐 — primary). ARCHITECTURE 불변식에 기록.
- 테스트: task 별 hostPath·volumeMounts 집합(미리보기·실행), dsync 는 모든 task 양쪽, `role_volumes` 누락 4형태 fail-closed.
  런처가 양쪽을 마운트한다고 고정하던 `test_volumes_sync_two_storages` 교체. 백엔드 3498 통과.
- 실증(k1, dmsk8s): 실행 vcjob 의 task 별 hostPath = launcher `/cephfs/dms/artifacts` · source-worker `/cephfs-third` + base ·
  destination-worker `/cephfs-secondary` + base. nsync(root, cephfs-third → cephfs-secondary) 미리보기 files 6·bytes 38 → 확인 →
  Succeeded 6·38, 목적지 트리·md5 원본과 동일.
- 실증(ssc d185 — dms-ssc 체크아웃을 dms-w1 이 빌드, api·controller 만 릴리스, 에이전트 d179·잡 이미지 d183 그대로): nsync(root,
  cephfs-third(dms-w1·w2) → cephfs-secondary(dms-w3)) 미리보기 vcjob task 별 hostPath 가 k1 과 같은 모양 → 미리보기 files 6·
  bytes 41 → Succeeded 6·41, 목적지 트리·md5 동일. **주의**: 릴리스가 Applied 를 보고한 직후 낸 첫 시도는 아직 종료 중이던 옛
  d184 컨트롤러가 집어 옛 모양(전 task 양쪽)으로 vcjob 을 만들어 FailedMount — 취소 후 재시도에서 통과(롤링 업데이트 동안
  두 컨트롤러가 겹친다; 롤아웃 뒤 ~30초 두고 실증한다).

### ✅ 테스트베드 분리 — k8s 테스트베드(LDAP 없음) 배포 자산·가드 컨텍스트 대조 — **완료·실증**(2026-10-21~22, k0 → k1)

사용자 요청: 「dms-k8s 브런치에 대한 테스트베드가 추가로 필요해. 지금 w4,w5 워커노드를 없애고, dms-k8s 용 VM 테스트베드를 추가로
구성하고 싶어 … 레지스트리 VM 은 dms-ssc 와 공유해도 될거같아. 테스트베드 다시 리부팅됬을때 재구축 가능해지도록 스크립트 및 파일들,
문서들 전부 업데이트해줘.」 + 「참고로, dms-k8s 브런치에서는 LDAP 을 사용 안할거야. 이점은 확실히해둬.」 VM·CephFS·레지스트리·DB·
호스트 네트워크는 testbed 저장소 몫이고, 이 저장소는 DMS 층만 바꿨다(이미지 입력 src·frontend·deploy/k8s·deploy/docker 무변경 —
재빌드 불필요).

- **신규 오버레이 `deploy/overlays/testbed-k8s`**(k8s-cp1 + k8s-w1·w2, 컨텍스트 `dmsk8s`): 이미지 `pkg-01:5001/dms`·`dms-agent`
  `k0`, 잡 이미지 `pkg-01:5001/dms-mpifileutils:k0`, `DMS_BUILD_REGISTRY=pkg-01:5001`(빌더 `buildah:stable` 만 :5000 공용),
  공인 풀 `10.20.20.101/32`, ingress tls `dms-k8s.local`, 특권 목록 root,admin,mason·허용 true·보조 그룹 false 명시. **LDAP 없음**:
  ConfigMap 의 LDAP 키 7개 null 삭제(빈 값·"false" 가 아니라 삭제 — base 값은 같은 망의 ssc 용 slapd 를 가리킨다), 에이전트
  `command: ["dms"] args: ["agent"]`(nslcd 엔트리포인트 우회) + LDAP env 6개 `$patch: delete`, 시크릿 4키. 결과 = root 잡만
  동작, 비 root 는 `ldap_not_configured` fail-closed. api/controller Deployment 는 패치하지 않는다(securityContext 계약).
  태그 관례 `kN`(k0 = :5000 의 d184·d179·d183 시드 복사), bump 는 main(→ dms-k8s)에서만 — ssc 오버레이 `testbed` 는
  dms-ssc 쪽 소관(빌드 소스 전환 뒤).
- **가드 컨텍스트 대조**(`deploy/overlays/guard-images.sh`): 두 클러스터가 같은 ns(dms)라 엉뚱한 클러스터 apply 가 새 사고
  유형이다. `--context <ctx>`(또는 `KUBE_CONTEXT`) 지원 + 오버레이의 `kube-context` 파일(testbed = dms, testbed-k8s = dmsk8s)과
  겨눈 컨텍스트(없으면 현재 컨텍스트)가 다르면 **exit 4**. kubeconfig 에 없는 컨텍스트는 exit 2(라이브 조회 실패가 "첫 설치"로
  보여 통과하던 틈). 라이브 조회는 그 컨텍스트로 고정하고, 통과 메시지가 `kubectl --context <ctx> apply -k <dir>` 을 찍는다.
  `kube-context` 가 없는 오버레이(prod·ssc 사이트, install.sh)는 종전 그대로 — 단 셸에 남은 `KUBE_CONTEXT` 가 현재 컨텍스트와
  다르면 exit 4(install.sh 는 현재 컨텍스트로 apply 하므로 가드가 다른 클러스터의 라이브를 보고 통과시키면 안 된다; 명시
  `--context` 는 존중).
- **`deploy/testbed/dms-shared-fs.yml`**: 대상 그룹을 `-e dms_cluster=<접두>`(기본 dms)로 — `dmsk8s` 면 k8s-w1 이 `/k8s` 하위
  트리(=/cephfs) 안에 같은 레이아웃을 만든다. LDAP 없는 클러스터에는 LDAP uid/gid 소유 픽스처를 만들지 않고 root 소유분만
  `/cephfs/managed/e2e` 에(`dms_fs_ldap_fixtures`, 기본은 dms 일 때만 켬). `--list-hosts`·로컬 변수 평가로 두 클러스터 확인.
- **`deploy/docker/registry-setup.sh`**: `registry` 단계는 라이브와 다른 레지스트리(podman·이름 볼륨·삭제 꺼짐)를 만들던 것을
  거부(exit 2)하고 testbed 저장소의 `registry_server` 롤(`make registry`, docker registry:2·삭제 켬, :5000/:5001)로 안내.
  `nodes` 단계(CRI-O 신뢰)는 **컨텍스트 필수**(`--context dms|dmsk8s`, 기본 컨텍스트 하드코딩 제거) + 컨텍스트별 레지스트리
  목록(dmsk8s = 5000+5001) + `SSH_OPTS`.
- 문서: deploy/README(맨 위 「테스트베드는 둘이다」 표, §0 레지스트리, §2 플레이북 두 클러스터, §3·§3b·§4/5 `--context`·
  k8s 시크릿 4키, §8 클러스터별 build_source_path, §9-4 오버레이별 bump 브랜치, §10 CA·릴레이 영속 위치, **신규 §13 k8s 테스트베드
  런북**(LDAP 없음이 뜻하는 것·배포 게이트 `grep -ci ldap == 0`)), ARCHITECTURE(오버레이 목록·가드 exit 4·testbed-k8s LDAP 삭제
  불변식), CLAUDE.md(태그 bump 규칙: 오버레이 ↔ 클러스터 ↔ 브랜치, `--context` 상시), BACKLOG(overlays/prod 의 LDAP 필수 vs 무
  LDAP dms-k8s — 사용자 결정 대기, "LDAP 끔" 명시 스위치, 태그 제안 kN 미인식, 점검 스크립트 컨텍스트, 레지스트리 경로 접두 미지원).
- 테스트: 신규 `tests/test_testbed_k8s_overlay.py`(정적: base 의 LDAP 키·에이전트 LDAP env 가 전부 삭제되는지 — base 에 키가 생기면
  빨간불; 렌더(`kubectl kustomize`, kubeconfig 무관): 'ldap' 0건·에이전트 `dms agent`·시크릿 참조 SHARED_TOKEN 뿐·이미지 전부
  :5001·빌드 레지스트리·VIP·호스트·ssc 오버레이는 LDAP 유지·모든 오버레이가 자리표시자 없이 렌더) — LDAP null 한 줄·명령
  오버라이드를 지우는 변이에 빨간불 확인. 신규 `tests/test_guard_images_context.py`(가짜 kubectl 로 실 스크립트: 교차 apply
  exit 4·현재 컨텍스트 대조·KUBE_CONTEXT·인자 우선·빈 kube-context exit 2·없는 컨텍스트 exit 2·이미지 변경 exit 3 유지·고정 없는
  오버레이 종전 동작·저장소 고정값) — 대조를 끄거나 `--context` 고정을 빼는 변이에 빨간불 확인.
- **라이브 적용(2026-10-21~22)**: testbed 저장소 순서(그 저장소 ARCHITECTURE §19.2)대로 — w4·w5 드레인·삭제, k8s-cp1·k8s-w1·
  k8s-w2 신설, pkg-01 공유(:5001 레지스트리·`dmsdb_k8s`·CephFS `/k8s`), 두 클러스터 `READ_ONLY=1 make validate-<c>` PASS=105.
  DMS(dmsk8s): k0 시드 → `dms-shared-fs.yml -e dms_cluster=dmsk8s` → 시크릿 4키 → 가드(`--context dms` 는 exit 4, `dmsk8s`
  통과) → apply → 무 LDAP 게이트(ConfigMap·api/controller env 의 ldap 0, 에이전트 `dms agent`, nslcd 없음) → 관리자·시드
  (스토리지·정책·sync 쌍 9, 빌드 노드 k8s-w1·소스 main 체크아웃). ssc 의 빌드 소스는 `/home/mason/dms-dev/dms-ssc` 로 전환.
- **실증(dmsk8s)**: root scan(dscan 5파일 16 MiB)·dsync(미리보기 5 → 16 MiB)·rm(미리보기 walk 5 → 5) Succeeded, 비 root scan →
  `ldap_not_configured` 거부(의도). nsync 는 FailedMount 로 멈춰 DMS 결함을 찾았다(위 「nsync 미리보기·실행 파드가 반대편 스토리지를 마운트하던 문제」) — 고친 **k1**(첫 포탈 빌드:
  k8s-w1 이 main 체크아웃 928b3c9 를 빌드해 :5001 에 dms·dms-agent·dms-mpifileutils push, 릴리스 에이전트 → api·controller·잡
  이미지, live == 동봉 매니페스트로 드리프트 배지 없음)에서 nsync 미리보기 6 → Succeeded 6항목·38 B, 목적지 md5 원본과 동일.
  오버레이 `testbed-k8s` newTag·`DMS_JOB_IMAGE` = k1 로 bump 후 가드 통과·재적용(재롤아웃 없음). 실증 데이터·요청 정리.
- **재부팅 리허설**: hostnet 유닛 stop/start(포탈 불통 → 복구), k8s-w1(2회)·k8s-w2 재부팅 — 첫 재부팅에서 CephFS 가 **두 겹**
  마운트되는 testbed 부팅 가드 경합을 찾아 고쳤다(testbed 저장소; `post-boot-check` 가 겹 수도 본다). 이후 한 겹·~40s Ready,
  `make post-boot-check` PASS=72. **호스트 콜드부팅은 미실증**(세션에서 호스트 재부팅 불가 — BACKLOG).
- **2026-10-22 testbed 보안·리뷰 반영(문서만)**: testbed 저장소가 빌드 소스 export 를 체크아웃별·워커 IP 한정으로 좁히고(부모
  `/home/mason/dms-dev` 에 testbed 의 SSH 키·CA 키·secrets 가 있었다), `client.dmsk8s` 를 OSD `namespace=k8s` 로도 가두고,
  PostgreSQL pg_hba 를 DB 별로(LDAP 로그인이 `dmsdb_k8s` 에 못 닿게) 바꾼 것에 맞춰 deploy/README §1-빌드 소스·§10·§13-1 갱신.
  k8s 포탈의 평문 릴레이 :8081 삭제(force-ssl-redirect 가 포트 없는 :443 = ssc 포탈로 보냈다). 표·「Unresolved values」의
  비밀번호 위치 정정: `dmsapp`·`search_dms` 비밀번호는 testbed `group_vars/all.yml` 인라인 데모값이고 `secrets.yml` 엔
  `postgresql_app_password_k8s` 만 있다(2026-08-30 「CHANGEME 마커」 서술은 사실이 아니었다). 테스트베드 CA 영구 사본 위치 명기.

---

> 2026-10-21 이전 기록은 [`history/changelog-2026-09-to-10-21.md`](history/changelog-2026-09-to-10-21.md)(2026-09-07 ~ 10-21)와 [`history/changelog-2026-08-slices.md`](history/changelog-2026-08-slices.md)(2026-08 슬라이스)에 있다.
