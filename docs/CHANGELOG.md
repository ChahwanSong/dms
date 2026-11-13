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
> 「Phase 1~3 … d23~」)이고, k8s d23 은 2026-11-05 라이브(아래 첫 항목)다. 이 파일에서 d179~d194(세 자리)는 ssc 계열, d1~d23 은 k8s 계열이다.
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
  표 전체가 오류 한 줄 — 표 유지 + 「자동 갱신 실패」 띠 · B09 scan 「스캔한 파일」이 실제로는 전체 항목 수 — 「스캔한 항목(디렉터리·링크
  포함)」·배치 「항목 수」 · B10 상태 필터 「Running」은 늘 0건(요청 상태는 Running 이 되지 않음) — 목록에서 뺌 · B11 처리량 라벨이 UTC —
  KST 표시, 일 버킷은 KST 날짜로 접음(`metrics_series.kst_day_buckets`) · B12 k8s 「스냅숏 기준」 줄 390px 넘침.
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
