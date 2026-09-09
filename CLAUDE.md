# CLAUDE.md

Guidance for Claude Code (claude.ai/code) working in this repository.

DMS: 여러 스토리지 백엔드(CephFS/GPFS/WekaFS)와 Kubernetes 클러스터에 걸친
**스토리지 인벤토리**와 **데이터 잡**(scan/sync/rm)을 관리하는 시스템. FastAPI +
PostgreSQL(제어면) + React 포탈 + 노드 에이전트 + Volcano gang-scheduled 잡 러너.

## 어디를 볼 것인가 (문서 지도)

문서는 **성격별로 분리**돼 있다. 질문 종류에 따라 여기서 시작해라:

| 알고 싶은 것 | 문서 |
|---|---|
| **지금 시스템이 어떻게 도는가** (유지보수 진입점) | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) + 코드의 「왜」 주석 |
| **어떻게 배포·운영하는가** | [`deploy/README.md`](deploy/README.md) |
| **남은 일** | [`docs/BACKLOG.md`](docs/BACKLOG.md) |
| **무엇을·언제·왜 지었나** (빌드 역사) | [`docs/CHANGELOG.md`](docs/CHANGELOG.md) |
| **왜 그렇게 설계했나** (원문 근거, 동결) | [`docs/history/`](docs/history/) |

**드리프트 규칙**: 현재 동작의 진실은 **코드 + 모듈 docstring**이 정의한다.
`ARCHITECTURE.md`는 얇은 지도(모듈·불변식을 가리킴, 메커니즘 재서술 안 함)이고,
`docs/history/`는 왜 그렇게 됐는지의 근거다. 코드를 바꿀 때 불변식
(`docs/ARCHITECTURE.md`의 「불변식」)이 걸리면 그 문서도 함께 갱신한다 — 나머지는
코드가 스스로 말한다.

## 코드에서 반드시 지키는 규약 (위반하면 시스템이 깨진다)

자세한 건 `ARCHITECTURE.md`에, 여기엔 가장 자주 밟는 것만:

- **DB 가 신뢰 경계다.** `create_job` 은 무검증 INSERT 라, tool·경로가 변조될 수 있다는
  전제로 방어한다(stepper 층1 `unknown_tool`, `_abs` fail-closed, 러너 allowlist).
- **null(모름) ≠ 실패, 0 은 정상값.** truthy 검사(`if x:`)로 이 셋을 뭉개지 마라 —
  카운트·로그·큐에서 특히. `is None` 으로 명시 비교.
- **사유 코드는 양쪽 등록**: `frontend/src/lib/reasonCodes.json` 과 `api.ts`
  REASON_MESSAGES 둘 다(양방향 계약 테스트). AST 추출기는 `reason_code=` **키워드
  리터럴**만 읽는다 — 위치 인자로 넘기면 커버리지 밖.
- **비밀번호는 평문으로 저장·전송·에코하지 않는다.** 저장은 `accounts._hash_password`
  (scrypt), 전송은 프런트 `postWithSealedPassword` ↔ 백엔드 `_password_from`
  (password_enc 봉인, 라이브는 평문 422 거절). 비밀번호를 받는 새 훅·엔드포인트는
  반드시 이 통로를 거친다 — `apiSend` 에 password 를 직접 실으면 그 경로만 평문이
  되고 아무 테스트도 빨간불이 아니다. 봉인 상수는 `password_transport.py` 와
  `passwordTransport.ts` 가 바이트 단위로 같아야 한다(대조 테스트 있음).
- **새 DB 컬럼은 CREATE TABLE 과 `_ensure_columns` 양쪽**(구형 DB 업그레이드 경로).
  전수 열거 그물(`test_migrations.py`)이 테이블·인덱스 추가·삭제를 잡는다.
- **매니페스트-우선 배포**: 이미지 태그를 먼저 bump·커밋하고 **그 커밋에서** 빌드한다
  (`Dockerfile.dms` 가 `deploy/k8s` 를 이미지에 COPY). 슬라이스 34부터 **빌드가 동봉
  매니페스트를 빌드 태그로 자동 스탬프**하므로(빌드하는 이미지 줄만), 포탈에서 태그를
  지정해 빌드하면 그 수동 bump 없이도 배포 시 live == manifest 가 되어 드리프트 배지가
  안 뜬다 — 단 그 태그를 실제로 굴리려면 `deploy/k8s` 의 git 값도 그 태그로 맞춰야
  `kubectl apply` 가 새 태그를 배포한다(자동 b태그는 릴리스 화면으로 굴린다).
- **워크트리 공유 중 커밋은 `git commit -- <경로>`**(pathspec). `git add` 로 인덱스를
  거치면 다른 세션 커밋에 파일이 섞인다(실제 사고 있었음, BACKLOG §5).
- **PYTHONPATH 함정**: venv 의 `dms` 편집설치는 **본 저장소** src 를 가리킨다. 워크트리
  코드를 테스트·실행하려면 `PYTHONPATH=<워크트리>/src` 를 명시해야 한다.
- **런타임은 airgap 이다** (배포 환경은 사내망 — 인터넷 불가. 빌드 타임만 인터넷 가능).
  포탈·백엔드가 런타임에 로드하는 모든 리소스(폰트·아이콘·스크립트·이미지)는 **번들에
  포함**돼야 한다 — CDN `<link>`·외부 fetch 금지. 폰트는 @fontsource 류 셀프호스팅,
  아이콘은 번들되는 라이브러리(lucide-react)나 인라인 SVG. 프론트 빌드 후
  `dist/index.html` 에 외부 URL 참조가 없는지 확인하는 것이 배포 게이트다.
- **제어면(api·controller)은 root 로 돈다 — 파일시스템 권한은 2차 방어가 아니다**
  (2026-09-09; 운영 아티팩트 base 가 root:root 라 uid 0, cap 전부 drop, 이미지 fs
  읽기 전용). 인가는 DB(`_owned_job`/`require_admin`)와 코드 봉쇄뿐이다:
  - artifact base 아래 파일을 여는 코드는 API·컨트롤러 모두 **반드시**
    `artifact_files.open_artifact_fd`(단일 open O_NOFOLLOW|O_NONBLOCK → S_ISREG →
    nlink==1 → 소유자 st_uid∈{0, 요청자 uid} → fd realpath 봉쇄 → 크기 상한)를 거치고
    `owner_uid=job_owner_uid(job)` 를 넘긴다(None 은 fail-closed 404). `open(path)`·
    `os.path.exists`·`os.access`·subprocess 로 사용자 영향권 경로를 만지지 마라.
  - 관리자가 주는 경로는 역할 게이트 + prefix allowlist(`DMS_ARTIFACT_BASE_ALLOWED_PREFIXES`,
    `artifact_base.allowlist_reason`) + realpath 검사 셋 다.
  - uid/gid 부재를 0 으로 기본값 처리하지 않는다 — 부재는 거부(`stepper.identity_problem`,
    `identity_missing_at_step`). uid 0 자체는 privileged 짝이 맞으면 정당.
  - root 는 매니페스트 **컨테이너 수준**(40-api/41-controller)에만 — Dockerfile `USER
    65532`·migrate 는 유지하고 `test_release_manifest_contract.py` 가 모양을 고정한다.
    "강화" 한답시고 runAsNonRoot/65532 를 넣으면 운영 base 에서 즉시 회귀한다.
  - 전체 규칙은 `docs/ARCHITECTURE.md` §7 「root 제어면」.

## legacy (제거됨 — git 히스토리에서 열람)

이전 DMS 구현 전체를 보존하던 `legacy/` 는 2026-08-30 사용자 지시로 트리에서
제거했다(새 구현이 완결돼 참조 종료). 과거 설계·운영 제약이 필요하면 git
히스토리(`git show f6cce77:legacy/...`)와 `docs/history/` 를 본다. legacy 코드를
새 구현으로 복사·import 하지 않는 원칙은 그대로다.

## 테스트·검증

- 백엔드: `PYTHONPATH=<워크트리>/src <venv>/bin/python -m pytest tests -q` (~7분, 1280+ passed)
- 프론트: `cd frontend && npx vitest run` (266+ passed) · `npx tsc -b`
- e2e(실 브라우저): `cd frontend && npm run test:e2e` (9 passed, ~25s) — **CI 없음, 수기 게이트**
