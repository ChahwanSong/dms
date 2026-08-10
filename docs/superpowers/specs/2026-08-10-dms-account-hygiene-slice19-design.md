# 슬라이스 19 — 계정 위생 설계

두 가지를 낸다: (A) **공유 토큰 actor 스푸핑**을 닫는다 — 토큰 보유자가
`x-dms-actor: root` 로 잡을 **uid 0** 으로 돌리는 경로가 배포 기본값에서 이미 열려
있다. (B) **하드 계정 삭제** API·UI 를 추가하고 삭제·강등·비활성화 세 경로에 안전장치를
건다. 백로그(`docs/superpowers/BACKLOG.md:89-96`)의 슬라이스 19 항목이다.

## 1. 실측으로 확인한 전제 (코드 직접 확인)

1. **토큰 인증은 `x-dms-actor` 를 거의 그대로 신뢰한다.** `api/auth.py:39-49` —
   `Bearer` + `tokens_match` 통과 후 `actor = (x-dms-actor or "").strip() or
   "shared-token"`(:46)이고 **유일한 거절 규칙은 `token:` 접두 → 401**(:47-48)뿐이다.
   반환은 `Identity(actor, role="admin", auth="token")`(:49). 그 `token:` 가드의
   주석(`auth.py:5-7`)은 스스로 **"감사 로그 표식 위장 방지"** 라고 적는다 — 특권
   사칭 방지가 **아니다**. 이 가드가 사칭을 막는다는 오해를 이 문서가 명시적으로 깬다.

2. **특권 승격은 배포 기본값에서 이미 열려 있다.** `config.py:113-114`:
   `allow_privileged_requesters=True`, `privileged_requesters={"root","admin"}` —
   **env 오버라이드가 아니라 기본값**이다. `identity.py:49`
   `privileged = allow_privileged and requester_id in privileged_requesters`,
   `:62-63` `if privileged: return ResolvedIdentity(owner, 0, 0, (), True)` →
   **uid=0/gid=0**.

3. **그 uid 는 실제로 파드에 박힌다.** preflight 파드 `securityContext`:
   `runAsUser=ident.uid`, `runAsGroup=ident.gid`(`execution_manifests.py:303-304`).
   그리고 `_auto_chown`(:56-69)은 `if ident.get("privileged") ...: return []`(:67)라
   **특권이면 비특권 sync 에 주입되는 `--chown` 을 생략**한다(:69).

4. **완전한 스푸핑 경로.** 공유 토큰 + `x-dms-actor: root` → `POST
   /api/user/requests`(`routes_requests.py:75`)가 `requester_id=identity.actor`
   (:103)로 요청 생성 → 플래너 `resolve_job_identity`(`planner.py:151-158`) → uid=0.
   **더 넓은 표면**: `x-dms-actor` 를 실제 LDAP 사용자명으로 두면 잡이 **그 사용자의
   uid/gid 로** 돈다(비특권 경로 `identity.py:64-78`). root 는 최악의 한 경우일 뿐이다.

5. **플래너 계정 admission 은 스푸핑을 못 막는다.** `planner.py:139-141` — 계정 row 가
   **존재하고 disabled 일 때만** 거부한다. `root`/`admin` 이름의 포탈 계정은 보통
   없으므로 admission 이 무판정으로 통과한다.

6. **잡 신원은 plan 시점에 구워진다.** `planner.py:193-197`가 `worker_pool.identity`
   와 `precondition`(`requester_id`/`owner`)을 JSON 으로 박고, `data_jobs` 에는
   owner/actor 컬럼이 **없다**(`migrations.py:129-180`). 삭제는 소급되지 않는다.

7. **`accounts` 는 6 컬럼, FK 는 저장소 전체에 0 건.** `migrations.py:226-232`:
   username(PK)/password_hash/role/email/disabled/created_at. `src/`·`deploy/` 전체
   `FOREIGN KEY`/`REFERENCES` **0 건**(grep). `user_scan_paths`(:233-239)는 username 을
   **관례로만** 참조한다(제약 없음).

8. **삭제 연산이 어디에도 없다.** `repositories/accounts.py` 는 create/verify/
   set_password/get/list/set_role/set_disabled 만 있다 — delete 는 리포지토리·라우트
   양쪽 어디에도 없다. `disabled` 컬럼 = **소프트 삭제 선례가 이미 존재**한다.

9. **`_guard_self` 에 마지막 관리자 보호가 전무하다.** `routes_accounts.py:17-20` 은
   `identity.actor == username` 인 **자기 role/disabled 변경만** 막는다. 마지막
   관리자 보호는 삭제·강등·비활성화 어느 경로에도 없다. 변경 라우트는
   set_role(:28)·set_disabled(:46) 둘뿐, 삭제 라우트는 없다.

10. **세션은 Starlette 서명 쿠키뿐.** `api/app.py:43-44`(`dms_session`) — 서버측
    세션 저장소·무효화 목록이 없다. 삭제된 계정은 **세션 경로에서만** `accounts.get()`
    이 None → 401(`auth.py:54-57`)로 걸리고, **토큰 경로는 계정 존재와 무관하게 계속
    admin**(`auth.py:41-49`)이다.

11. **`shared_token` ≠ `admin_token`.** 둘 다 env 필수(`config.py`). `shared_token` 은
    Bearer 인증(`auth.py:41`), `admin_token` 은 계정 부트스트랩 전용
    (`routes_auth.py:57`)이다. 에이전트는 `Bearer <shared_token>` +
    `x-dms-actor: node:<name>`(`agent/runner.py:57`)로 쓰고, 에이전트 라우트는
    `identity.actor == f"node:{node_name}"` 를 강제한다(`routes_agent.py:18`).

## 2. 핵심 결정

### 2.1 스푸핑은 실재하며 배포 기본값에서 이미 열려 있다

§1-1~4 가 경로 전체다. 강조점 셋: (a) **env 오버라이드에 의존하지 않는다** — 기본값
`allow_privileged_requesters=True` + `privileged_requesters={root,admin}` 만으로 열려
있다. (b) root 는 **최악의 한 경우**일 뿐, 임의 LDAP 사용자명으로도 그 사용자 권한을
탈취한다. (c) 현재 `token:` 접두 거절(`auth.py:47-48`)은 **감사 로그 위장만** 막는다 —
특권 사칭을 막는다고 읽으면 정확히 틀린다.

### 2.2 차단 — 토큰 경로 actor 를 에이전트 전용으로 좁힌다

1. **토큰 인증 경로의 `x-dms-actor` 는 `node:<이름>` 형태만 허용한다.** 이름부는
   `routes_agent.py:9` 의 `_NODE_NAME_RE` 를 그대로 재사용한다(DNS-1123, 이미 잡이
   검증하는 바로 그 규칙). 그 외 값은 **400** 으로 거절. 빈 값·공백만은 기존대로
   `shared-token` 정규화(`auth.py:46`, `test_api_control.py:108-127` 유지).

2. **심층 방어**: `resolve_job_identity` 의 특권 승격을 **`auth='session'` 인 요청에만**
   허용한다 — `auth='token'` 이면 `privileged` 승격을 강제로 끈다. 1 번이 이미 막지만,
   `requester_id` 가 다른 경로로 들어올 여지를 닫는다.

3. **결과(의도된 것)**: 토큰으로는 actor 가 `shared-token` 또는 `node:<이름>` 뿐이고
   **둘 다 LDAP 에 없어** 잡 제출이 사실상 막힌다. 공유 토큰은 기계용이지 사람의 잡
   제출용이 아니다. **admin API 는 토큰으로 계속 된다** — 끊기는 것은 잡 제출과
   actor 사칭뿐이다. 따라서 앞으로 **잡을 제출하는 수기 실증만** 세션 로그인(쿠키
   curl)으로 하고, 운영·조회용 admin curl 은 그대로 쓴다(§6).

4. **파급(실측했다, 과장하지 않는다)**: 토큰은 actor 와 **무관하게** admin 역할을
   주므로(`auth.py:49`) 이 변경이 admin API 접근을 끊지 않는다 — 끊는 것은 토큰
   호출자가 **자기 이름을 고르는 능력**뿐이다. 테스트 21 개 파일이
   `ADMIN = {"Authorization": "Bearer tok-shared", "x-dms-actor": "ops"}` 모듈 상수를
   쓰는데(`test_api_admin_accounts.py:1`, `test_api_policies.py:3` 등), **헤더 키 하나만
   지우면 그대로 통과**한다(actor 는 `shared-token` 으로 정규화). 세션 로그인 이관은
   필요 없다. 실제로 고쳐야 하는 단언은 5 곳뿐이다: `test_api_releases.py:231` 과
   `test_api_control.py:74` 의 `actor == "token:ops"` → `"token:shared-token"`,
   `test_api_auth.py:21,76` 의 `ops-debug` actor, 그리고 `token:alice` 가 오늘
   401(`test_api_auth.py:85-90`)인데 새 규칙에선 **더 넓은 게이트에 흡수돼 400** 이
   되는 것. 1·2 번을 계약 테스트로 고정한다.

### 2.3 계정 삭제 — 하드 삭제

`DELETE FROM accounts` + `user_scan_paths` **연쇄 정리**. FK 가 0 건(§1-7)이라
`requests.requester_id`/`actor`, `batches`, `state_transitions.actor`,
`audit_log.actor` 의 문자열 actor 는 **그대로 남는다** — 이건 버그가 아니라 **이력
보존**이다. `user_scan_paths` 만 연쇄하는 이유: 계정 소유 리소스이고, 모든 조작이
`identity.actor` 의 행만 다루며 타인 행은 404 로 숨기므로(`routes_scan_paths.py:1-3,
107-112`) 소유자가 사라지면 **아무도 볼 수 없는 데드 로우**가 된다.

**안전장치 셋**:

1. **자기 삭제 차단** — 단 §2.2 를 고쳐야 비로소 신뢰할 수 있다. 지금 `_guard_self`
   (`routes_accounts.py:17-20`)는 `identity.actor == username` 을 비교하는데, 토큰
   경로는 actor 를 자유 지정할 수 있어(`auth.py:46`) 공격자가 actor 를 대상과 다르게
   두면 가드가 아예 발동하지 않는다. §2.2 로 토큰 actor 가 `node:<이름>`/`shared-token`
   로 좁혀지면(실계정 아님) 이 가드가 **세션 경로에서만** 의미를 갖는다. 이 의존관계를
   명시한다.

2. **마지막 활성 관리자 보호** — 삭제뿐 아니라 **역할 강등·비활성화 세 경로 전부**에
   적용한다(현재 어느 경로에도 없는 구멍, §1-9). "활성 관리자" = `role='admin' AND
   disabled=0`. 그 수가 1 이고 대상이 그 하나면 409. 공유 토큰이 항상 admin 이라 완전
   잠금은 아니지만, **사람 admin 0 명은 여전히 사고**다.

3. **비종단 요청이 있으면 409** — 잡 신원은 plan 시점에 구워져(§1-6) 삭제가 소급되지
   않는다. 삭제 후 그 잡이 실패하면 **소유자 없는 잡**이 된다. `requester_id = username`
   이고 상태가 `TERMINAL_REQUEST_STATES`(`domain.py:21-24`) 밖인 요청이 하나라도 있으면
   거부한다(`requests.find_active`(`repositories/requests.py:92-100`)와 같은
   NOT-IN 패턴).

**감사**: `mutation_class='account'`, `operation='delete'`,
`before_state` 에 계정 스냅샷. `accounts.get()`(`repositories/accounts.py:65-69`)이
이미 `password_hash` 를 SELECT 에서 빼므로 스냅샷은 **자연히 password_hash 제외**다.

## 3. 화면

- `AccountsList.tsx` 의 각 행 「작업」 열에 **삭제 버튼**을 추가한다. 클릭 시 확인
  다이얼로그: 사용자명을 재입력해야 활성화된다(오조작 방지). 자기 계정 가드 UI 가
  이미 `AccountsList.tsx:38,57-60` 에 있으므로 그 관례를 따른다.
- **프론트 가드는 보안 경계가 아니다** — 서버가 §2.3 을 다시 강제한다.
- **비활성 사유 표기**: 자기 계정("자기 계정은 삭제할 수 없습니다"), 마지막 활성
  관리자("마지막 관리자는 삭제할 수 없습니다")를 각각 다른 문구로 낸다.
- 삭제가 409 로 거부되면(비종단 요청 보유) 그 사유를 그대로 표면화한다 — "진행 중인
  요청이 있어 삭제할 수 없습니다".

## 4. 오류 처리

- **`x-dms-actor` 게이트**: `node:<이름>` 아님 → **400 `invalid_actor`**. 빈 값·공백은
  400 이 아니라 `shared-token` 정규화(옆문 유지 금지, §2.2-1).
- **삭제 라우트**: 대상 부재 → 404 `account_not_found`(존재 확인을 가드보다 먼저,
  `routes_accounts.py:31-34` 관례). 자기 삭제 → 409 `cannot_delete_self`. 마지막 활성
  관리자 → 409 `last_active_admin`. 비종단 요청 보유 → 409 `account_has_active_requests`.
- **강등·비활성화**: 기존 409 `cannot_lock_self` 는 유지하고, 마지막 활성 관리자
  가드(409 `last_active_admin`)를 **set_role·set_disabled 양쪽에** 추가한다.
- 감사 실패나 부분 삭제를 막기 위해 삭제 + `user_scan_paths` 정리 + 감사 기록은
  **한 트랜잭션**으로 묶는다(`set_role` 이 이미 쓰는 `with self._db.transaction()`
  관례, `repositories/accounts.py:87-94`).

## 5. 테스트

- **`x-dms-actor` 계약**: `node:<이름>` 통과, 그 외 400, 빈 값/공백 → `shared-token`
  정규화(`test_api_control.py:108-127` 유지), 에이전트 리포트 경로가 계속 동작
  (`node:<이름>`)하는지. 특권 승격이 `auth='session'` 에서만 일어나는지.
- **기존 admin 테스트 정리**: 21 개 파일의 `ADMIN` 상수에서 `x-dms-actor` 키를 지우고
  (토큰만으로 admin 유지) 전부 통과하는지. actor 를 단언하던 5 곳(§2.2-4)은 새 값
  `token:shared-token` 으로 갱신한다 — **단언을 지우지 않는다**(감사 표식이 계속
  기록되는지가 이 변경의 회귀 지점이다).
- **삭제**: 정상 삭제가 `accounts`·`user_scan_paths` 를 함께 비우고 감사 행
  (`operation='delete'`, `before_state` 에 password_hash 없음)을 남기는지. 문자열
  actor(`requests`/`audit_log`)가 **남는지**(이력 보존 단언).
- **안전장치**: 자기 삭제 409, 마지막 활성 관리자 삭제·강등·비활성화 각각 409 +
  상태 불변, 비종단 요청 보유 계정 삭제 409. 관리자 2 명 중 1 명 삭제는 통과.
- **프론트**: 자기 계정·마지막 관리자 버튼 비활성화와 사유 문구, 사용자명 재입력
  확인 다이얼로그가 재입력 불일치 시 삭제를 막는지.

## 6. 실증 (테스트베드)

1. **고치기 전 RED**: 공유 토큰 + `x-dms-actor: root` 로 sync 요청을 넣어 preflight
   파드 `runAsUser=0` 이 실제로 나오는 것을 확인한다(스푸핑 재현).
2. **고친 뒤**: 같은 요청이 **400 `invalid_actor`** 로 막히는지.
3. **에이전트 무손상**: `node:<이름>` 경로로 리포트가 계속 들어오는지 — 이게 깨지면
   노드 리포트가 전부 멈춘다(가장 먼저 확인).
4. **백로그가 지목한 실제 피해 제거**: 슬라이스 3 실증이 만든 임시 관리자
   **`s3verify`**(`BACKLOG.md:91`)를 세션 로그인한 다른 관리자로 실제 삭제한다.
5. **마지막 관리자 보호**: 관리자를 하나만 남긴 뒤 삭제·강등 시도가 409 로 거부되는지.
6. **비종단 요청 가드**: 비종단 요청을 가진 계정 삭제가 409 로 거부되는지.

**잡을 제출하는** 수기 실증은 이후 세션 로그인(쿠키 curl)으로 한다. 운영·조회용
admin curl 은 `Bearer` 토큰만으로 그대로 동작한다 — §2.2-3 의 귀결이다.

## 7. 이 슬라이스에서 하지 않는 것

- **회원가입 메일 인증**(`routes_auth.py:20-30`, :22 더미). **의도적 보류** — 추후
  회사 메일 인증으로 교체 예정이다. 미인지 결함이 아니라 결정이다(`BACKLOG.md:94-96`).
- 토큰 **발급/회전 체계**(`shared_token`/`admin_token` 은 정적 사전공유 비밀 유지).
- **서버측 세션 저장소**(§1-10). 삭제된 계정의 즉시 무효화는 세션 경로에서만 되고,
  토큰 경로는 §2.2 로 이미 잡 제출이 막히므로 별도 저장소 없이 감내한다.
- 계정 **병합/이름 변경**.
- **삭제된 계정의 이력 익명화**(§2.3 은 이력 보존이 결정 — actor 문자열을 남긴다).
