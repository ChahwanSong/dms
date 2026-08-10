# DMS 백로그

이 저장소에는 로드맵 문서가 없었다. 범위는 원본 설계
(`specs/2026-08-02-dms-clean-slate-design.md`)와 슬라이스별 design+plan 쌍에 흩어져
있고, 미완 항목은 각 설계의 "이 슬라이스에서 하지 않는 것", 플랜의 "알려진 위험",
SDD 레저의 `minor (deferred)`, `deploy/README.md`의 미해결 값에 흩어져 있었다.

**이 파일을 만든 이유**: SDD 레저(`.superpowers/sdd/*/progress.md`)는 `.gitignore`가
`*`라 git에 들어가지 않는다. 실제로 **슬라이스 11·12·13의 레저가 이미 소실**됐고 —
그 슬라이스들이 파킹한 마이너 항목 기록은 복구 불가다. 코드 내 TODO/FIXME는 사실상
0건이라 백로그를 상기시켜 줄 장치가 아무것도 없었다. 앞으로 **파킹하는 항목은 레저가
아니라 이 파일에 적는다.**

갱신: 2026-08-10. 기준 커밋: `main` = 슬라이스 15 + nsync 후속까지 병합됨.

---

## 0. 현재 상태

- 슬라이스 1~16 완료.
- 테스트베드는 **전 이미지 `d26` 로 통일**(잡 러너 `dms-mpifileutils:d26`, 제어면
  `dms:d26`, 에이전트 `dms-agent:d26`). 태그 체계를 dNN/jobNN 분리에서 단일 dNN 으로
  바꿨다 — 세 이미지가 같은 소스 트리에서 나오고 agent 는 나머지 둘을 같은 태그로
  참조해 빌드되므로 분리가 오히려 함정이었다.
- ✅ 슬라이스 15 nsync 카운트 파서 **실증 완료**(d27, 잡 ace0581d):
  `{"files": 10, "bytes": 50}` + 컬럼 채워짐. 이로써 네 도구 전부(dsync·dscan·drm·
  nsync) 검증됨. cephfs-third(w1-3) -> cephfs-secondary(w4-5) 는 노드가 겹치지 않아
  placement 가 nsync 를 고른다 — 재현 시 목적지 이름을 매번 새로 잡아야 실제 복사가
  일어나 카운트가 0 이 아니다.
- 네트워크 지표는 **물리 인터페이스만** 합산으로 정정됨(설계 §2.6, d27 실증:
  전체 2,806,890,347,761 vs 물리 2,802,248,354,570 — 가상 4.64GB 제외).

---

## 1. 예정 슬라이스

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

### 슬라이스 17 «큐 가시성»
1. **Volcano 큐 현황 대시보드**(사용자 요청): 대기 중 작업 갯수, 대기 시간, 통계.
   슬라이스 14 비목표(`slice14-design.md:79,153`: "코드·RBAC 없음, CRD 읽기 + Role
   변경 필요"). 원본 설계 §300이 "Volcano 큐/우선순위"를 요구.
2. **전역 큐 대기 집계**. `runs` 테이블이 死物이다 — `migrations.py:69`가 만들지만
   읽기·쓰기 0건. 그래서 큐 대기는 요청 상세에서만 유도되고 전역 집계는 풀스캔이라
   금지됐다(`slice14-design.md:63-68`). 원본 설계 §293이 요구한 지표.
   → `runs` 부활 또는 `data_jobs`에 인덱스된 파생 컬럼 중 택일(설계 시 결정).

### 슬라이스 18 «아티팩트 경로 설정» (사용자 요청)
- 포탈에서 **아티팩트 저장 경로 설정** + **가능여부 검증**(실제로 써도 문제없는지).
- 현재는 ConfigMap 환경변수 `DMS_ARTIFACT_BASE_URI=file:///cephfs/dms/artifacts` 고정.
- **설계 시 반드시 다룰 것**: 경로를 바꾸면 **기존 잡의 아티팩트를 못 읽는다**.
  `execution_volcano.py`가 `self._artifact_base`로 `summary.json` 경로를 재구성하므로
  (`:207-222`) 옛 잡의 요약/로그 열람이 깨진다. 마이그레이션/이중 조회/잡별 base 기록
  중 택일 필요. 검증은 "컨트롤러·API·잡 파드 세 곳에서 쓰기 가능한 공유 FS인가"를
  봐야 하며 노드별 마운트 상태(에이전트 리포트)와 교차 확인해야 한다.

### 슬라이스 19 «계정 위생» (사용자 요청, 일부 제외)
- **계정 삭제 API + 포탈 UI**. 슬라이스 9 비목표(`slice9-design.md:39-43`). 실제 피해:
  슬라이스 3 실증이 만든 임시 관리자 **`s3verify`가 아직 살아 있다**(삭제 수단 없음).
- **공유 토큰 actor 스푸핑**. 토큰 보유자가 `x-dms-actor: root`로 uid 0을 얻을 수 있다
  (`deploy/README.md` 미해결 값). 세션 기반 actor로 전환 검토.
- ❌ **회원가입 메일 인증은 이 슬라이스에서 제외** — 사용자 지시: 추후 회사 메일
  인증으로 교체할 계획이므로 지금 손대지 않는다. (현재 `routes_auth.py:22` 더미,
  코드 검증 없이 가입 가능. **의도적 보류**이며 미인지 결함이 아니다.)

---

## 2. 미분류 백로그 (테마별)

### 2.1 러너 / 실행
- **nsync 실증 재확인**: 파서는 커밋됐으나 `job5` 재빌드 + 실 nsync 잡으로
  `files/bytes` 채워짐 확인 필요.
- **vcjob 런처 파드 로그 열람 불가** — 슬라이스 5 비목표(`409 log_not_available`).
  실행 단계 진단이 10슬라이스째 아티팩트 전용.
- **dscan 총바이트 없음** — 리포트 스키마에 없다(크기 히스토그램뿐).
  `bytes_total`은 sync 전용으로 남는다(`slice15-design.md:121`).
- **소급 백필 없음**(슬라이스 15 이전 잡), **프리뷰 카운트 DB 미승격**,
  **파일별 상세·에러 카운트·전송률 없음**.
- `storages.managed_root = "/"` 허용 → `_abs`가 `//team/data` 생성 가능
  (phase3c `progress.md:24`, 검증 강화 미이행).
- `_abs()` 스토리지 결측 폴백이 로그를 안 남김(phase3c `:22`).
- 고아 복구 쿼리에 `LIMIT` 없음 — 크래시루프 대량시(phase3c `:29`).
- `tool_argv` 미지 도구가 `drm` 분기로 흘러감(phase3c `:6`) — 상류 enum 검증에만 의존.
  **파괴적 경로의 fail-open 형태**.
- `imagePullPolicy: IfNotPresent` + 태그 재사용 = 노드 캐시 stale(phase3c `:88`).
  고유 태그 관례로만 완화됨.

### 2.2 포탈
- 슬라이스 1 FAST-FOLLOW 7건(`.superpowers/sdd/2026-08-04-dms-portal-slice1/progress.md:41-43`,
  레저 소실 위험 있어 여기 옮김): `StatusPill`이 잡 상태 전용이라 스토리지 `Healthy`가
  중립색(설계 §5 "정상=초록" 위반); `api.ts` 401 분기 중복; `Login.tsx`의 무가드
  `as ApiError` 캐스트; `RequestDetail` 로딩 상태 없음; 공유 `useCancelJob`으로 취소
  오류 미표시·확인 오류가 다이얼로그 닫힘에 리셋 안 됨; `Home`이 `me.isError` 미확인;
  요청 무효화 2건 접두 중복.
- 미구현 기능면: 아티팩트 **다운로드**(바이너리 스트리밍)·삭제·보존 UI; 배치 **CSV
  업로드**(현재 프론트 파싱)·결과 내보내기·템플릿·rm 배치; 고급 sync 옵션
  (`chmod`/`chown`/`bufsize`/`batch_files`) 폼 노출; 배치 생성 폼의 스토리지 드롭다운;
  에이전트 설정 푸시; 알림/경보(슬라이스 14 비목표 — "대시보드는 표시만").
- Sparkline 유효점 1개면 bare `M` → 빈 SVG(슬라이스 14 파킹, 도달 가능·영향 경미).
- **의존성 권고(의도적 보류, `frontend/README.md`)**: react-router `GHSA-qwww-vcr4-c8h2`
  high — 현재 어떤 `react-router-dom` 버전도 두 취약 범위를 동시에 피하지 못함.
  재검토 조건: `react-router-dom@8.3.0` 이상 릴리스. vite/vitest 체인 critical 1건은
  dev 전용이며 semver-major 업그레이드 필요(슬라이스 12 §7 범위 밖).
  **`npm audit fix --force` 실행 금지.**

### 2.3 운영 / 배포
- **클러스터 내 registry 미구축** — 원본 설계 §246은 "클러스터 내 registry:2",
  실제는 호스트 `pkg-01:5000`(슬라이스 11 비목표: "독립된 인프라 작업").
- **`DMS_JOB_IMAGE`는 포탈 롤아웃 불가** — ConfigMap 갱신 + 소비자 재시작이라
  슬라이스 13 범위 밖. 여전히 `20-config.yaml` 수기 편집.
- **태그 동기화가 5파일 수기** — 템플릿 계층 없음(의도적, `deploy/README.md`).
  "하나라도 빠지면 그 컴포넌트만 옛 이미지로 돈다." **슬라이스 16 이후 완화**:
  라이브와 매니페스트가 갈라지면 대시보드 드리프트 배지가 표시하고,
  api/controller 는 initContainer 와 본 컨테이너 이미지가 다르면 계약 테스트가
  RED 다(그 divergence 는 배지가 못 잡는 유일한 구멍이라 테스트로 막았다).
  여전히 못 잡는 것: 매니페스트를 아예 안 고친 채 포탈 롤아웃만 한 상태는
  배지가 잡지만, 그걸 **무시하면** 다음 apply 가 되돌린다.
- 빌드 레이어 캐시 미보존·멀티아치 없음(슬라이스 11 비목표). 빌드 노드는 **인터넷
  egress 필요**(npm/dl.k8s.io/github/PyPI/Debian) — 격리망에서 실패.
- **롤백 버튼 없음**(의도적 — 이력에서 옛 태그 재선택).
- `/cephfs` hostPath `type: Directory` — 비-cephfs 노드가 스케줄 풀에 들어오면 파드
  admission 실패.
- `DMS_LDAP_BIND_DN`/`_PW` 공란(익명 바인드) 미해결.
- 레지스트리 태그 검증 fail-open — 레지스트리 다운 시 `unknown_tag` 통과 →
  `ImagePullBackOff`(`deploy/README.md` §9-7).
- pod GC 86400s가 **프리플라이트 파드 로그(유일한 진단 사본)** 를 파괴
  (슬라이스 10 Important 2).
- DaemonSet 롤아웃 **600s 타임아웃 미측정** — 5노드 순차가 넘기면 거짓 실패
  (슬라이스 13 플랜 알려진 위험).
- Prometheus/Grafana 배포는 **의도적 제외**(포탈 대시보드로 대체).

### 2.4 데이터 모델
- `runs` 테이블 死物(§1 슬라이스 17에서 처리).
- **`data_jobs.created_at` 인덱스 없음** → `job_stats`의 모든 GROUP BY가 테이블 스캔.
  현 규모 무해, 수십만 건이면 필요(슬라이스 14 플랜 위험).
- `by_storage`가 `COALESCE(storage_name, destination_storage)` — sync를 **도착지 기준**
  으로 센다. 설계가 기준을 명시하지 않은 침묵의 해석.
- KPI 의미 변화(요청 50건 즉석 계산 → 창 내 잡 집계) — 옛 화면과 숫자가 다르다.

### 2.5 테스트 / CI
- **e2e 전무**(Playwright 없음) — 15슬라이스째 단위 테스트 + 수기 실증.
- `KubernetesClient`가 `# pragma: no cover` — 슬라이스 14가 추가한 `threading.Lock`
  이중검사가 무테스트 코드.
- 마이그레이션 **ALTER 경로 일반 회귀 커버리지 갭** — 슬라이스 14가 파킹했고 그
  파킹 항목이 실제 프로덕션 500을 냈다. 슬라이스 15가 `_widen_count_columns`만 보강.
- 슬라이스 15 잔여: `text or ""` 반쪽 약속 무검증; `test_execution_volcano` 픽스처가
  구식 summary 모양; `information_schema` 쿼리에 `table_schema` 미필터(단일 스키마
  배포에서만 안전).
- 슬라이스 14 잔여: Sparkline NaN/Infinity 무검증; `by_state:null` 테스트가
  `Array.isArray`와 `?? []`를 구분 못함.
- 슬라이스 9 Task 6은 진짜 RED 단계가 없었음(구현자 자진 신고).
- 슬라이스 1~4 테스트 부채 다수(`jobState.test`의 `PreviewExpired`/`Planning`/
  `Scheduled` 누락, `idx_requests_batch` 미단언, `BatchDetail` 확인-POST 단언이
  `waitFor` 밖이라 플레이키 위험, `PolicyDialog` tool 필드 `aria-label` 없음,
  `useDenylist` URL 미인코딩 등).

### 2.6 프로세스
- **SDD 레저는 git에 없다**(`.superpowers/sdd/.gitignore` = `*`). 슬라이스 11·12·13
  레저 소실. → 앞으로 파킹은 이 파일에 기록.
- **슬라이스 13 실증 체크리스트가 전부 미체크**(`deploy/README.md` §9, 8개 `[ ]`).
  레저도 없어 컨트롤러 자기 갱신 수렴(핵심 실증)이 시연됐는지 확인 불가.
- **포탈 빌드는 GitHub에 푸시된 커밋만 본다**(`--depth 1 --branch`) — 로컬 전용
  커밋은 빌드 불가. 미푸시 상태와 겹치면 이 브랜치 자체를 빌드할 수 없다.

---

## 3. 규약

- 파킹할 항목은 **이 파일**에 적는다(레저는 워크트리와 함께 사라진다).
- 각 항목은 근거를 `파일:줄`로 남긴다.
- 의도적 보류(회원가입 메일 인증, Prometheus, 롤백 버튼, 템플릿 계층, cron 예약)는
  "미구현"이 아니라 **결정**이므로 그렇게 표시한다.
