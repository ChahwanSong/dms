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

## legacy/ — 읽기 전용, 설계 참고용

`legacy/` 에는 이전 DMS 구현 전체가 보존돼 있다(소스·테스트·문서·이전 CLAUDE.md).

- **읽기 전용.** `legacy/` 아래 어떤 파일도 수정·이동·삭제하지 않는다. 새 파일도 안 넣는다.
- **설계 참고용으로만.** 도메인 지식·운영 제약·과거 결정의 출처다. 새 구현에서 legacy
  코드를 import 하거나 복사하지 않는다 — 필요한 개념은 새로 설계해 구현한다.

## 테스트·검증

- 백엔드: `PYTHONPATH=<워크트리>/src <venv>/bin/python -m pytest tests -q` (~7분, 1280+ passed)
- 프론트: `cd frontend && npx vitest run` (266+ passed) · `npx tsc -b`
- e2e(실 브라우저): `cd frontend && npm run test:e2e` (9 passed, ~25s) — **CI 없음, 수기 게이트**
