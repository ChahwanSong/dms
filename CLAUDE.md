# CLAUDE.md

Guidance for Claude Code (claude.ai/code) working in this repository.

DMS: 여러 스토리지 백엔드(CephFS/GPFS/WekaFS/DDN Lustre/Pure Storage/NetApp/Dell OneFS/NFS — 등록
라벨일 뿐 직접 경로 해석은 mount_path·managed_root 만 본다. **dms-k8s 예외**: backend_type 은 PV 경로 프리셋 키이기도
하다 — `pv_paths.PRESETS`(gpfs·onefs·nfs), 2026-10-25)와 Kubernetes 클러스터에 걸친
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
- **공유 토큰은 계정·배포 경로를 못 만진다**(2026-10-07). 모든 노드 에이전트가 가진 Bearer 토큰(role admin,
  auth=token)으로 계정을 다시 만들거나 릴리스로 옛 이미지에 롤백하면 특권 게이트가 통째로 무효가 된다 — 계정 변경은
  `accounts_session_required`, 빌드·릴리스·컨트롤 상태·레지스트리 삭제·artifact base 변경·작업(요청) 삭제는
  `auth.require_session_admin`(`admin_session_required`). 그런 새 변경 라우트를 만들면 `require_admin` 이 아니라
  이 의존성을 쓴다. 특권 목록(env `DMS_PRIVILEGED_REQUESTERS` ∪ 계정 화면 부여, 2026-10-12 — 자격 판정은 `privileged.privileged_names`,
  계정 보호는 `privileged.protected_names`(비활성 부여 계정 포함)를 거친다) 이름 계정은 특권 세션 관리자만
  관리(`guard_privileged_account`). admin 역할만으로
  root 자격을 주지 마라 — 자격 부여는 특권 세션 관리자만(`routes_accounts.set_root_grant`). 배치 sync·rm 자식은 배치
  확인 도장이 있는 미리보기만 실행된다(`docs/ARCHITECTURE.md` 「배치 재검토·확인 게이트」 — 확인 게이트 연산은
  `domain.confirm_gated` 하나로 가른다). 자동 확인을 켠 배치
  (`batches.auto_confirm_by`, 2026-10-11 — 기본 꺼짐, 켜기는 특권 세션 관리자만)도 orchestrator 가 **같은**
  `BatchesRepository.confirm` 을 부를 뿐이다 — 도장·회차 CAS 를 건너뛰는 지름길을 만들지 말고, 단건 sync·rm 에 자동
  컨펌을 넣지 마라(사용자 결정: 단건은 사람이 승인). rm **배치**는 자동 확인을 켤 수 있다(2026-10-21 사용자 결정 — 같은
  날의 「rm 은 늘 사람 확인」을 뒤집었다): sync 와 같은 게이트에 켤 때 「되돌릴 수 없음」 동의(`acknowledge_irreversible:
  true`, 없으면 422 `batch_rm_auto_confirm_ack_required`, 감사 행에 남는다)가 더 붙는다. 연산 집합은 `domain.auto_confirmable`
  하나(켜는 라우트·PreviewReady 분기·`auto_confirm_hold` 공용 — scan·미지 연산은 자동 확인 없음)다 — 세 곳에 따로 쓰지 마라.
- **새 DB 컬럼은 CREATE TABLE 과 `_ensure_columns` 양쪽**(구형 DB 업그레이드 경로).
  전수 열거 그물(`test_migrations.py`)이 테이블·인덱스 추가·삭제를 잡는다.
- **요청·잡을 가리키는 새 테이블·컬럼은 요청 삭제 목록에도**(2026-10-08). FK 가 0건이라 빠지면 지운 요청의 행이 조용히
  고아가 된다 — `repositories/request_purges.py` 의 `PURGED_TABLES` + `_purge_locked`(delete_terminal·delete_batch 공용 몸통, 늦게 들어올 수 있으면 `finish` 의
  scrub 도)에 DELETE 를 넣거나, 지우지 않는 이유와 함께 `PURGE_EXEMPT_TABLES` 에. 그물
  (`test_request_delete_covers_every_reference_table`)은 `request_id`·`job_id`·`entity_id` 이름의 컬럼만 본다 — 다른 이름으로
  요청·잡 id 를 담으면 그물 밖이다.
- **base 매니페스트는 사이트 중립, 실 태그는 오버레이에**(2026-09-14). `deploy/k8s` 의
  `image:`·`DMS_JOB_IMAGE` 는 자리표시자(`set-by-overlay.invalid/<img>:set-by-overlay`)뿐이고 계약 테스트가
  고정한다 — 여기에 실 레지스트리·태그를 커밋하면 git pull 한 다른 사이트가 그 값을
  배포한다(실사고). 태그 bump 는 테스트베드 오버레이의 `newTag`(+`patch-config.yaml` 의
  `DMS_JOB_IMAGE`), 사이트는 `values.env`. **테스트베드는 둘이다**(2026-10-21, deploy/README.md 「테스트베드는 둘이다」):
  `deploy/overlays/testbed` = **ssc 클러스터**(컨텍스트 `dms`, pkg-01:5000, 태그 `dN`, LDAP 사용) — ssc 빌드 소스가
  dms-ssc 체크아웃으로 넘어간 뒤로는 **dms-ssc 쪽에서만** bump(전환 전에는 main); `deploy/overlays/testbed-k8s` =
  **k8s 클러스터**(컨텍스트 `dmsk8s`, pkg-01:5001, 태그 `dN` — d1 부터, 2026-10-26 전엔 `kN`, **LDAP 없음** — 사용자 결정) — **dms-k8s 에서만**(2026-10-22 분리 뒤 이 클러스터의 빌드 소스는 dms-k8s 체크아웃)
  bump. 한 커밋에서 둘을 같이 올리지 마라. 두 클러스터가 같은 네임스페이스(dms)라 **kubectl 은 늘 `--context` 를
  명시**한다(current-context 는 dms; dmsk8s 를 current 로 만들지 마라). 적용은
  항상 `kubectl --context <ctx> apply -k deploy/overlays/<site>` — raw `kubectl apply -f deploy/k8s` 는
  pull 에서 실패하도록 돼 있다(k8s 클러스터에 raw `20-config.yaml` 은 지운 LDAP 키를 되살린다). apply 전
  `deploy/overlays/guard-images.sh <dir> --context <ctx>` 가 "렌더 이미지 != 라이브" 면 거부하고(exit 3 — 포탈 릴리스 뒤
  오버레이 태그를 안 맞춘 재적용이 되돌리는 사고 방지; 의도한 변경은 `ALLOW_IMAGE_CHANGE=1`), 오버레이의
  `kube-context` 파일과 다른 컨텍스트면 거부한다(exit 4). testbed-k8s 의 LDAP 삭제(ConfigMap 키 null·에이전트
  `dms agent`·LDAP env 삭제)는 `tests/test_testbed_k8s_overlay.py` 가 고정한다 — base 에 LDAP 키·env 를 더하면 그
  오버레이에도 삭제를 더하라. 포탈 빌드는 이미지에 COPY 되는 사본만 스탬프하므로
  (`build_manifests`) 포탈에서 태그를 지정해 빌드·릴리스하면 live == 동봉 매니페스트라
  드리프트 배지가 안 뜨고, 동봉값이 자리표시자·타 레지스트리면 배지는 "모름"(None) 이다.
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
    `identity_missing_at_step`). uid 0 자체는 privileged 짝이 맞으면 정당. **예외: 보조 gid 키 부재 = `[]`**
    (2026-10-08 — 배포 전에 계획된 잡의 정상 모양이고 더 좁은 권한이다, ARCHITECTURE §7-9). 부재를 거부로
    "강화" 하지 마라 — 진행 중 잡이 배포 순간 전부 끊긴다.
  - **잡의 root 여부는 제출 시점에 확정돼 payload `run_as_root` 로만 흐른다**(2026-09-30):
    planner·stepper 는 `identity.privilege_policy`(`run_as_root is True` 또는 배치 자식)만
    본다 — 특권 목록에 있다는 것만으로 root 가 되지 않는다. 서버(`routes_requests.submit`)는
    **명시 `run_as_root: true` 만** root(자격 필요, 없으면 403), 생략·false 는 비 root 다.
    "관리자 기본 root" 는 **포탈**(SubmitJob `rootEffective`)이 정해 관리자에겐 확정값을 항상
    명시로 싣는다: 자격 있는 관리자는 기본 root, 단 **다른 실행 신원**(owner_username)을
    지정하면 기본은 그 사용자의 LDAP uid/gid. "관리자 + 실행 신원 = 일반 사용자" 가 무조건
    root 로 돌아 남의 디렉터리 소유권을 소스 소유로 덮어쓴 프로덕션 사고가 이 규칙의 이유다 —
    그 경로를 기본 root 로 되돌리지 말고, 서버의 생략 기본값을 root 로 바꾸지 마라(옛 포탈
    탭·me 판정 불일치가 화면에 없던 root 로 돈다). **dms-k8s 예외 — k8s 신원 모드**(`DMS_IDENTITY_MODE=k8s`,
    2026-10-25 설계 D1~D4, `k8s_access` 모듈 docstring): LDAP 이 없어 **모든 단건 잡이 root** 이고 서버가
    `run_as_root: true` 를 싣는다(생략 = root, 명시 false = 422 `run_as_root_required`). 이 모드에서 root 허용은
    `identity.root_allowed`(세션 인증 요청인가) 하나를 제출·계획(`resolve_job_identity` REQUESTED)·컨펌 세 지점이
    쓰고, 권한은 root 자격이 아니라 **k8s RoleBinding 맵핑**(`k8s_access`·`repositories.k8s.access_for` — 일반
    사용자는 PV 주소만, 면제 = `can_run_as_root` 의 관리자 + 특권 목록/부여 + 세션)이 판정한다 — 모드 스위치를
    둘로 쪼개지 마라(「root 인데 권한 판정 없음」). 세션 조건은 빼지 마라(공유 토큰 → 모든 경로 root). ldap 모드(코드
    기본)는 위 규칙 그대로다. 배치 자식(IF_ELIGIBLE)은 두 모드 모두 특권 목록 판정. k8s 모드는 그룹 denylist 를
    평가하지 않는다(그룹 규칙 추가 422 `denylist_group_unsupported`).
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
