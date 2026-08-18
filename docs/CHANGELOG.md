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

