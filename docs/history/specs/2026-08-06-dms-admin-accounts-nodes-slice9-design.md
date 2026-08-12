# DMS 포탈 — 슬라이스 9 (관리자 화면 마감: 계정 관리 + 노드 대시보드) 설계

2026-08-06. 상위 스펙 `2026-08-02-dms-clean-slate-design.md` §8(포탈 — 관리자)의 하위 구현
문서. 슬라이스 1~8은 구현·실증·배포 완료. 충돌 시 상위 스펙이 이긴다.

## 0. 배경 & 범위

상위 스펙 §8의 관리자 목록 중 아직 화면이 없는 것은 **계정 관리**, **노드 대시보드**,
**빌드·릴리스**다. 이 슬라이스가 앞의 둘을 채운다(빌드·릴리스는 §7 별도 슬라이스).

- **노드**: 백엔드가 **이미 완비**돼 있다 — `GET /api/admin/nodes`(에이전트 신선도 포함),
  `GET /api/admin/nodes/{name}/reports`(이력). 화면만 없다.
- **계정**: `accounts` 테이블에 `disabled` 컬럼이 있고 `AccountsRepository.verify`가 실제로
  그것을 검사한다(비활성 계정은 로그인 불가). 하지만 **역할 변경·비활성화 API가 없고**, 목록
  조회 API도 없다. 생성은 `x-admin-token` 전용 부트스트랩 경로뿐이다.

### 0.1 실측으로 확인한 결정적 제약 — 세션 무효화

`current_identity`(`src/dms/api/auth.py:23-25`)는 **세션 쿠키만 보고 계정 테이블을 다시 확인하지
않는다.** 따라서 계정을 비활성화해도:

- 새 로그인은 막힌다(`verify`가 `disabled`를 본다) ✅
- **이미 로그인된 세션은 계속 동작한다** ❌

"계정 비활성화" 버튼이 그 상태로 있으면 운영자를 속이는 것이다. 이 슬라이스는 **세션 경로에서
계정을 재확인**해 비활성화가 즉시 효력을 갖게 만든다.

### 0.2 담는 것

- **계정 관리 API**(백엔드 신규) — 목록·역할 변경·비활성/활성 토글. 전부 감사 로그 기록.
- **세션 계정 재확인**(백엔드 신규) — 세션 인증 요청마다 계정이 존재하고 활성인지 확인.
  비활성/삭제면 `401`. Bearer 공유 토큰 경로는 계정과 무관하므로 그대로 둔다.
- **계정 관리 화면**(`/admin/accounts`).
- **노드 대시보드**(`/admin/nodes`) — 목록(신선도·마운트·도구 요약) + 노드 상세(디스크,
  마운트/도구 상태, 최근 리포트 이력).

### 0.3 비목표

- 계정 **생성** UI — 부트스트랩은 `x-admin-token` 경로가, 일반 가입은 `/api/auth/signup`이
  담당한다. 관리자가 임의 계정을 만드는 화면은 이번 범위 밖(비밀번호를 관리자가 정하는 흐름은
  별도 설계가 필요하다).
- 비밀번호 재설정·이메일 인증.
- 계정 **삭제** — 요청·감사 로그가 사용자명을 참조하므로 비활성화로 갈음한다.
- 노드 메트릭 **시계열 차트** — §9 대시보드 슬라이스 소관. 여기서는 최신값과 최근 이력 표.
- 에이전트 설정 변경(포탈에서 노드 설정 푸시).

## 1. 화면 지도 (admin 트리)

| 화면 | 경로 | 내용 |
|---|---|---|
| 계정 | `/admin/accounts`(신규) | 목록 + 역할 변경 + 활성/비활성 토글 |
| 노드 | `/admin/nodes`(신규) | 노드 목록(신선도·마운트/도구 요약) + 선택 시 상세 |

## 2. 백엔드

### 2.1 계정 관리 API (`routes_accounts.py` 신규)

```
GET   /api/admin/accounts                 -> [{username, role, email, disabled, created_at}]
PUT   /api/admin/accounts/{username}/role     {role: "user"|"admin"}  -> 갱신된 행
PUT   /api/admin/accounts/{username}/disabled {disabled: bool}        -> 갱신된 행
```

- `require_admin`. `AccountsRepository.list()`는 이미 비밀번호 해시를 빼고 반환한다 — 그대로 쓴다.
- 없는 계정 → `404 account_not_found`.
- `role`은 `user`/`admin`만(`422 invalid_role`).
- **자기 자신을 잠그지 못하게 한다**: 요청자가 자기 계정을 `admin → user`로 낮추거나
  비활성화하려 하면 `409 cannot_lock_self`. 마지막 관리자가 스스로를 내려 포탈을 잠그는 사고를
  막는다. (Bearer 공유 토큰 요청자는 `actor`가 계정이 아닐 수 있으므로 이름이 일치할 때만 건다.)
- 두 변경 모두 **감사 로그**에 before/after를 남긴다 — `mutation_class="account"`,
  `operation="role"`/`"disabled"`. 기존 `accounts.create`가 audit을 직접 INSERT하는 방식을
  따르되, 리포지토리에 `set_role`/`set_disabled`를 추가하고 거기서 기록한다.

### 2.2 세션 계정 재확인 (`auth.py`)

`current_identity`의 **세션 분기**에서만 계정을 확인한다:

```python
    session = request.session
    if session.get("username") and session.get("role"):
        account = request.app.state.repos.accounts.get(session["username"])
        if account is None or account["disabled"]:
            request.session.clear()
            raise HTTPException(status_code=401, detail="account_disabled")
        return Identity(actor=session["username"], role=account["role"])
```

- **역할도 계정 행에서 다시 읽는다** — 관리자가 역할을 낮추면 즉시 반영된다(세션 쿠키의 낡은
  role을 신뢰하지 않는다).
- 세션을 지워 다음 요청이 `not_authenticated`가 되게 한다.
- **비용**: 세션 인증 요청마다 기본키 조회 1회. `accounts.username`이 PRIMARY KEY라 인덱스가
  이미 있다. Bearer 경로는 이 조회를 하지 않는다.
- 프론트의 401 처리(`api.ts`가 `dms:unauthorized` 이벤트를 쏴 로그인으로 보냄)가 그대로 동작한다.

### 2.3 기존 그대로

`routes_nodes.py`(목록·이력), `AccountsRepository.create`/`verify`/`get`/`list`, 감사 로그
스키마는 **변경하지 않는다**. 노드 쪽은 백엔드 신규가 **0건**이다.

## 3. 프론트엔드

### 훅
- `features/accounts/useAccounts.ts` — `useAccounts()`, `useSetRole()`, `useSetDisabled()`
  (각각 `["accounts"]` 무효화).
- `features/nodes/useNodes.ts` — `useNodes()`(목록, `refetchInterval` 10초),
  `useNodeReports(name, enabled)`(지연 로드).

### 화면
- **AccountsList**(`/admin/accounts`) — 사용자명 / 역할 / 이메일 / 상태 / 등록일 / 작업.
  역할은 select(user/admin)로 즉시 변경, 상태는 "비활성화"/"활성화" 토글. `409
  cannot_lock_self`는 인라인 안내. 자기 행은 두 컨트롤을 **비활성화**해 왕복 전에 막는다.
- **NodesList**(`/admin/nodes`) — 노드별: 이름, 신선도(fresh/stale), 마지막 리포트 시각,
  마운트 요약(`Ready n/m`), 도구 요약(`Ready n/m`). 행을 열면 상세:
  - 마운트 표(스토리지·경로·status·reason)
  - 도구 표(이름·status·version·reason)
  - 디스크 표(스토리지·used/total, 사용률 %)
  - "최근 리포트" 버튼 → `useNodeReports(name, true)`로 지연 로드해 `reported_at` 목록 표시.
  - `identities`는 비어 있는 경우가 많으므로 있을 때만 렌더한다.
- stale 노드는 눈에 띄게 표시한다(`text-bad`) — planner의 어드미션이 신선도에 의존하므로
  운영자가 즉시 알아야 한다.

### 배선/타입
- `router.tsx`: `/admin/accounts`, `/admin/nodes`(둘 다 RequireRole admin).
- `AppShell.tsx`: admin 내비에 "계정", "노드" 추가.
- `lib/types.ts`: `Account`, `NodeInfo`(기존 `Node` 타입이 있으면 확장), `NodeReport` 추가.
- `lib/api.ts` reason 코드: `account_not_found`, `invalid_role`, `cannot_lock_self`,
  `account_disabled`("계정이 비활성화되었습니다"), `node_not_found`.

## 4. 테스트

- **백엔드**: 계정 목록에 **password_hash가 없음**을 명시 단언; 역할 변경·토글이 반영되고
  감사 로그에 before/after가 남음; 없는 계정 404; 잘못된 역할 422; **자기 잠금 409**;
  비관리자 403. 세션 재확인 — 비활성화된 계정의 **기존 세션이 401**이 되고, 역할을 낮추면
  다음 요청부터 admin 라우트가 403이 됨(이 두 개가 이 슬라이스의 핵심 단언이다).
- **프론트**: 계정 목록/역할 변경 PUT body/토글 PUT body; 자기 행 컨트롤 비활성; 노드 목록
  렌더와 stale 강조; 상세의 마운트·도구·디스크 표; "최근 리포트"를 누르기 전에는 요청 없음.

## 5. 배포/실증

마이그레이션 변경 없음 → migrate 재실행 불필요. 인증 경로(`auth.py`)가 바뀌므로 api 재배포는
필수이고, 컨트롤러는 영향 없지만 단일 이미지라 함께 d18로 올린다.

실증: 관리자로 계정 목록 확인 → 테스트 계정의 역할을 admin으로 올렸다가 되돌리기 →
**그 계정의 기존 세션이 즉시 401이 되는지**(비활성화 후 그 세션으로 요청) → 자기 자신 잠금
시도 409 → 노드 대시보드에서 5개 노드의 마운트/도구/디스크가 실데이터로 보이는지 → 노드 상세의
최근 리포트 이력.

## 6. 결정 기록

- 계정 비활성화가 **기존 세션까지 즉시 끊도록** `current_identity`의 세션 분기에서 계정을
  재확인한다. 그렇지 않으면 비활성화 버튼이 거짓말이 된다.
- 역할도 **세션 쿠키가 아니라 계정 행에서** 읽는다 — 강등이 즉시 반영된다.
- **자기 잠금 금지**(409 `cannot_lock_self`) — 마지막 관리자가 포탈을 잠그는 사고를 막는다.
- 계정 **생성·삭제 UI는 범위 밖**(비밀번호 흐름·참조 무결성 때문). 비활성화로 갈음한다.
- 노드는 백엔드 신규 0건 — 화면만 만든다. 시계열 차트는 §9 슬라이스로 미룬다.
