# 슬라이스 19 — 계정 위생 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** (A) 공유 토큰 actor 스푸핑을 닫는다 — 토큰 보유자가 `x-dms-actor: root` 로 잡을 **uid 0** 으로 승격하는 경로가 배포 기본값에서 이미 열려 있다. 토큰 경로의 `x-dms-actor` 를 `node:<이름>` 형태로만 좁히고(그 외 400), 특권 승격을 `auth='session'` 인 요청에만 허용해 심층 방어를 건다. (B) **하드 계정 삭제** API·UI 를 추가하고, 삭제·역할 강등·비활성화 세 경로에 「자기 대상」·「마지막 활성 관리자」·「비종단 요청 보유」 안전장치를 건다.

**Architecture:** 스푸핑 차단은 `api/auth.py:current_identity` 의 토큰 인증 분기 한 곳이 코어다 — 여기서 `x-dms-actor` 를 `node:<이름>`(에이전트가 다시 검증하는 바로 그 `_NODE_NAME_RE`)만 통과시키면 requester_id 가 사람 이름이 될 여지가 사라진다. 심층 방어는 `resolve_job_identity` 에 `session_authenticated` 파라미터를 더하고, 그 값을 요청에 실린 인증 방식으로 계산한다 — 요청은 인증 방식을 몰라 저장해야 하므로 `requests` 테이블에 `auth_method` 컬럼을 (CREATE TABLE·`_ensure_columns` 양쪽에) 더하고 제출·배치 두 생성 지점에서 채운다. 하드 삭제는 `repositories/accounts.py` 에 `delete`(accounts + user_scan_paths + 감사 한 트랜잭션)를 더하고, 라우트에서 존재 확인 → 자기 삭제 → 마지막 활성 관리자 → 비종단 요청 순으로 게이트한다. 마지막 활성 관리자 가드는 세 라우트가 공유하는 헬퍼로 뽑는다. 프론트는 `AccountsList.tsx` 각 행에 삭제 버튼 + 사용자명 재입력 확인 다이얼로그를 더한다.

**Tech Stack:** Python 3.11 표준 라이브러리(FastAPI 라우트 1건 추가 + 리포지토리 메서드 3건 + 마이그레이션 컬럼 1건), React 18 + Vitest(다이얼로그 1건 + 훅 1건 + 목록 버튼), 새 의존성 없음.

## Global Constraints

- 설계 문서: `docs/superpowers/specs/2026-08-10-dms-account-hygiene-slice19-design.md`. 플랜과 충돌하면 **설계가 이긴다**.
- **새 pip/npm 의존성 금지.**
- **새 DB 테이블 금지** — `tests/test_migrations.py:173` 이 `len(ALL_TABLES) == 20` 을 고정한다. 이 슬라이스가 저장하는 상태(요청의 인증 방식)는 기존 `requests` 테이블에 `auth_method` **컬럼**으로 더한다(테이블 추가 아님).
- **컬럼은 CREATE TABLE 과 `_ensure_columns` 양쪽에** 넣는다. 한쪽만 넣으면 기배포 DB 에서만 컬럼이 없다(슬라이스 14 가 실 500 으로 배운 교훈).
- **하드 삭제는 한 트랜잭션** — accounts 삭제 + `user_scan_paths` 정리 + 감사 기록을 `with self._db.transaction()` 하나로 묶는다(`repositories/accounts.py:87,:97` 의 기존 관례). 부분 삭제·감사 누락을 막는다.
- **감사 단언을 지우지 않는다** — actor 를 단언하던 곳은 새 값으로 **갱신**한다. 토큰 호출이 감사 로그에 계속 `token:` 접두로 남는지가 이 변경의 회귀 지점이다.
- **`legacy/` 아래는 읽기 전용** — 수정·이동·삭제·추가 금지, import 금지, 코드 복사 금지.
- 백엔드 테스트 명령(이 워크트리 전용, `.venv` 가 워크트리에 없다):
  `PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest`
  전체 스위트는 **포그라운드**로 Bash timeout 600000ms. **기준선 998 passed.**
- 프론트: `cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20/frontend && npx vitest run` (**기준선 215 passed / 48 files**), 타입체크 `npx tsc -b`.
- 주석은 **한국어**로 "왜"를 적는다(무엇이 아니라 왜).
- **클러스터 접근(kubectl) 금지** — 실증은 플랜 밖(별도 ops).
- **origin 으로 push 금지.** 커밋만 한다.

## 실측 고정값 (코드 직접 확인)

| 항목 | 값 |
|---|---|
| 토큰 인증 actor 신뢰 | `api/auth.py:36-59` `current_identity`. 토큰 분기(:39-49): `tokens_match` 통과 후 `actor = (x-dms-actor or "").strip() or "shared-token"`(:46), **유일한 거절은 `token:` 접두 → 401**(:47-48), 반환 `Identity(actor, role="admin", auth="token")`(:49) |
| 예약 접두 상수 | `api/auth.py:8` `_RESERVED_ACTOR_PREFIX = "token:"`. `audit_actor`(:23-27)가 `identity.auth != "session"` 일 때 이 접두를 붙여 표시용 actor 를 만든다 — 이 슬라이스에서 **audit_actor 는 안 건드린다** |
| `_NODE_NAME_RE` | `api/routes_agent.py:9` `re.compile(r"[A-Za-z0-9]([A-Za-z0-9.-]{0,252}[A-Za-z0-9])?$")`, 사용 `:16` `fullmatch`. routes_agent 는 auth 에서 `Identity, require_user` 만 import(`:5`)하고 **auth 는 routes_agent 를 import 하지 않는다** → 정규식을 auth 로 옮겨도 순환 import 없음 |
| 특권 승격 | `identity.py:42-78` `resolve_job_identity`. `privileged = allow_privileged and requester_id in privileged_requesters`(:49), `if privileged: return ResolvedIdentity(owner, 0, 0, (), True)`(:62-63) → uid=0/gid=0. **유일한 프로덕션 호출자는 `planner.py:153-158`** (grep: src 에 다른 호출 0건) |
| config 기본값 | `config.py:113` `allow_privileged_requesters=True`, `:114` `privileged_requesters=frozenset({"root","admin"})` — **env 오버라이드가 아니라 기본값** |
| requests 스키마 | CREATE TABLE `migrations.py:55-67`(컬럼 요청_id/commit_order/operation/requester_id/actor/resource_key/priority/payload/state/created_at/updated_at/batch_id). `_ensure_columns` 튜플 `:406-425`(마지막 요소 `("releases","progress","INTEGER")` :424), 존재 가드 `_column_exists` `:369-377` |
| requests.create | `repositories/requests.py:21-40`. INSERT 컬럼 목록 `:29-31`, 값 바인딩 `:32-37`. **호출자 2곳**: `routes_requests.py:102-104`, `batch_orchestrator.py:66-69` (src grep) |
| 종단 상태 | `domain.py:21-24` `TERMINAL_REQUEST_STATES = frozenset({Succeeded, Failed, Rejected, Conflict, Cancelled})` |
| NOT-IN 조회 패턴 | `repositories/requests.py:92-100` `find_active`, `:102-113` `active_referencing_storage` — `terminal = tuple(s.value for s in TERMINAL_REQUEST_STATES)` + placeholder 생성이 기존 관례 |
| accounts 스키마 | `migrations.py:226-232` — username(PK)/password_hash/role/email/disabled/created_at. FK 저장소 전체 0건(설계 §1-7) |
| accounts repo | `repositories/accounts.py`: `get`(:65-69, SELECT 에서 **password_hash 제외**), `_audit_account`(:76-82, 시그니처 `(operation, username, before, after, actor, now)` → `dump_json(before)`/`dump_json(after)`), `set_role`(:84-94), `set_disabled`(:96-104), 둘 다 `with self._db.transaction()`(:87,:97) |
| user_scan_paths | `migrations.py:233-239`(username 을 **관례로만** 참조, 제약 없음). `repositories/scan_paths.py:47-53` `delete_owned` 가 `DELETE FROM user_scan_paths WHERE id=:i AND username=:u` 관례 |
| routes_accounts | `api/routes_accounts.py`: `router = APIRouter(dependencies=[Depends(require_admin)])`(:6), `_guard_self`(:17-20, `identity.actor == username` → 409 `cannot_lock_self`), `set_role`(:28-43, 존재 확인 먼저 :33-34), `set_disabled`(:46-57). **삭제 라우트 없음**, 마지막 관리자 보호 **어느 경로에도 없음** |
| 특권 세션 테스트 | `tests/test_api_requests_privileged.py` 전부 **세션 로그인**(signup/login) 사용: `:22-23`, `:45`, `:54-55`. 특권 성공 경로 `test_admin_operator_with_flag_can_submit_for_other`(:39-47)는 세션 admin `ops`. 이 파일은 이 슬라이스 후에도 **그대로 통과해야** 한다 |
| planner 잡 신원 | `planner.py:186` `identity_dict = {**asdict(identity), "groups": ...}` → `worker_pool["identity"]` 에 uid/gid/privileged 가 박힌다(테스트가 `job["worker_pool"]["identity"]["uid"]` 로 읽는다, `test_planner.py:128`) |
| 감사 조회 | `repositories/control.py:159-161` `audit_entries(limit)` = `SELECT * FROM audit_log ORDER BY id DESC LIMIT :n`. 테스트는 `db.query("SELECT * FROM audit_log WHERE mutation_class = ...")` 직접 조회도 씀 |
| Dialog | `frontend/src/components/ui/Dialog.tsx` (radix `@radix-ui/react-dialog`, props `trigger/title/children/open/onOpenChange`). 관례 예 `features/denylist/DenyDialog.tsx`(open state + `useEffect` 초기화 + `field` 클래스 상수) |
| Account 타입 | `frontend/src/lib/types.ts:52-55` `{username, role, email: string|null, disabled: number, created_at}` |
| api.ts | `frontend/src/lib/api.ts` — `apiSend(method, path, body?)`(:190-191), `request` 가 204 → undefined(:185), REASON_MESSAGES 맵(:1-137), 이미 `cannot_lock_self`(:103)·`account_not_found`(:101)·`invalid_actor`(:27) 보유 |
| 21개 x-dms-actor 파일 | `grep -rln 'x-dms-actor' tests/` = 21건: test_agent_runner / test_api_admin_accounts / test_api_agent / test_api_artifacts / test_api_auth / test_api_builds / test_api_control / test_api_denylist / test_api_jobs / test_api_maintenance / test_api_metrics / test_api_nodes / test_api_policies / test_api_releases / test_api_request_cancel / test_api_requests / test_api_scan_paths / test_api_scan_path_stats / test_api_user_storages / test_auth_session_recheck / test_default_priority |

## 파일 구조

| 파일 | 책임 |
|---|---|
| `src/dms/api/auth.py` (수정) | 토큰 경로 `x-dms-actor` 게이트(`node:<이름>` 또는 빈값→shared-token, 그 외 400) + `_NODE_NAME_RE` 를 이 모듈로 승격 |
| `src/dms/api/routes_agent.py` (수정) | 로컬 `_NODE_NAME_RE` 정의를 지우고 `auth._NODE_NAME_RE` import (단일 출처) |
| `tests/test_actor_spoof_regression.py` (신규) | 스푸핑 재현 RED + 게이트 계약 + 정규식 단일 출처 |
| `tests/test_api_auth.py`·`tests/test_api_control.py`·`tests/test_api_releases.py` 외 21개 (수정) | ADMIN 상수에서 `x-dms-actor` 키 제거 + actor 단언 갱신 |
| `src/dms/migrations.py` (수정) | `requests.auth_method` 컬럼(CREATE + `_ensure_columns` 양쪽) |
| `src/dms/repositories/requests.py` (수정) | `create` 에 `auth_method` 파라미터 + `has_active_for_requester` |
| `src/dms/identity.py` (수정) | `resolve_job_identity` 에 `session_authenticated` 파라미터 |
| `src/dms/planner.py` (수정) | 요청의 `auth_method` 로 `session_authenticated` 계산해 전달 |
| `src/dms/api/routes_requests.py` (수정) | 제출 시 `auth_method=identity.auth` 전달 |
| `src/dms/batch_orchestrator.py` (수정) | 배치 materialize 시 `auth_method="token"`(비특권 기본) 전달 |
| `src/dms/repositories/accounts.py` (수정) | `delete`(하드 삭제 트랜잭션) + `active_admin_count` |
| `src/dms/api/routes_accounts.py` (수정) | `DELETE` 라우트 + 안전장치 3종 + 마지막 관리자 가드를 set_role·set_disabled 로 확장 |
| `frontend/src/features/accounts/useAccounts.ts` (수정) | `useDeleteAccount` 훅(DELETE) |
| `frontend/src/features/accounts/DeleteAccountDialog.tsx` (신규) | 사용자명 재입력 확인 다이얼로그(접근성 라벨) |
| `frontend/src/features/accounts/AccountsList.tsx` (수정) | 행별 삭제 버튼 + 자기/마지막 관리자 비활성 사유 |
| `frontend/src/lib/api.ts` (수정) | `cannot_delete_self`·`last_active_admin`·`account_has_active_requests` 문구 |

---

### Task 1: actor 스푸핑 차단 게이트 + 기존 테스트 정리

토큰 경로 `x-dms-actor` 를 `node:<이름>` 으로 좁혀 스푸핑(공유 토큰 + `x-dms-actor: root` → 잡 신원 uid 0)을 닫는다. 게이트를 켜면 `x-dms-actor: ops` 를 쓰는 기존 21개 테스트 파일이 400 으로 깨지므로, **게이트와 정리는 한 커밋**이어야 한다. 슬라이스 17 의 교훈("깨뜨려 RED 를 보기 전까지는 추측")을 이 보안 주장에 적용해, 먼저 스푸핑을 재현하는 테스트로 구멍이 살아 있음을 본 뒤 게이트로 뒤집는다.

**Files:**
- Create: `tests/test_actor_spoof_regression.py`
- Modify: `src/dms/api/auth.py`, `src/dms/api/routes_agent.py`
- Modify(테스트 정리): `tests/test_api_admin_accounts.py`, `tests/test_api_agent.py`, `tests/test_api_artifacts.py`, `tests/test_api_auth.py`, `tests/test_api_builds.py`, `tests/test_api_control.py`, `tests/test_api_denylist.py`, `tests/test_api_jobs.py`, `tests/test_api_maintenance.py`, `tests/test_api_metrics.py`, `tests/test_api_nodes.py`, `tests/test_api_policies.py`, `tests/test_api_releases.py`, `tests/test_api_request_cancel.py`, `tests/test_api_requests.py`, `tests/test_api_scan_paths.py`, `tests/test_api_scan_path_stats.py`, `tests/test_api_user_storages.py`, `tests/test_auth_session_recheck.py`, `tests/test_default_priority.py`

**Interfaces:**
- Consumes: `Identity`/`current_identity`(`auth.py`), `_NODE_NAME_RE`(`routes_agent.py:9` → `auth.py` 로 승격), `resolve_job_identity`(`identity.py`), `ControlRepository`.
- Produces: 토큰 경로 계약 — `x-dms-actor` 없음/빈값/공백 → actor `"shared-token"`; `node:<유효이름>` → 그대로; 그 외(root/alice/token:x…) → **400 `invalid_actor`**. `auth._NODE_NAME_RE` 가 유일 정의(routes_agent 재사용). 반환 `Identity` 모양·`role="admin"`·`auth="token"` 불변.

- [ ] **Step 1: 스푸핑을 재현·못박는 테스트를 쓴다**

`tests/test_actor_spoof_regression.py` (신규 파일 전체):

```python
"""공유 토큰 actor 스푸핑 회귀(슬라이스 19 설계 §1-4, §2.2).

두 반쪽을 각각 못박는다:
1) 결과가 실재한다 -- privileged_requesters 이름은 resolve_job_identity 에서 uid 0
   으로 승격된다(이 테스트는 게이트 전후로 계속 통과 -- 승격 메커니즘 자체는
   바뀌지 않는다. "페이로드가 진짜다"의 증거).
2) 공유 토큰 보유자가 그 이름을 스스로 고를 수 있다 -- 게이트 전에는 POST 가 202 로
   통과해 requester_id="root" 인 요청이 만들어진다(구멍이 살아 있다). 게이트 후에는
   400 invalid_actor 로 막히고 요청 자체가 만들어지지 않는다.

슬라이스 17 의 교훈: 테스트가 무언가를 붙잡는다는 주장은 실제로 깨뜨려 RED 를 볼
때까지 추측이다. 그래서 (2)의 RED(assert 202 == 400)가 스푸핑이 오늘 열려 있음을
증명한 다음에야 게이트를 신뢰한다."""
from fastapi.testclient import TestClient

import dms.api.auth as auth_mod
import dms.api.routes_agent as agent_mod
from dms.config import Settings
from dms.identity import resolve_job_identity
from dms.repositories.control import ControlRepository


TOKEN_ROOT = {"Authorization": "Bearer tok-shared", "x-dms-actor": "root"}
RM = {"operation": "rm", "storage": "s1", "target": "a",
      "options": {"recursive": True}}


def _client(db):
    settings = Settings(database_url="unused", shared_token="tok-shared",
                        admin_token="tok-admin", session_secret="sess")
    from dms.api.app import create_app
    return TestClient(create_app(settings, db))


def test_privileged_requester_synthesizes_root_uid_zero(db):
    # (1) 승격은 실재한다: root 는 privileged_requesters 기본값 멤버(config.py:114)이고
    # group deny 가 없으면 LDAP 없이 uid 0 으로 합성된다(identity.py:49,:62-63).
    control = ControlRepository(db)
    ident = resolve_job_identity(
        control, None, requester_id="root", owner_username=None,
        allow_privileged=True,
        privileged_requesters=frozenset({"root", "admin"}))
    assert ident.uid == 0 and ident.gid == 0 and ident.privileged is True


def test_shared_token_cannot_choose_privileged_actor(db):
    # (2) 게이트가 이 스푸핑을 닫는지. 게이트 전에는 POST 가 202 로 통과하고
    # requester_id="root" 인 요청이 생겨 (1)의 승격으로 이어진다 -- 그게 구멍이다.
    client = _client(db)
    r = client.post("/api/user/requests", headers=TOKEN_ROOT, json=RM)
    assert r.status_code == 400
    assert r.json()["detail"] == "invalid_actor"
    # 요청 자체가 만들어지면 안 된다(플래너가 소급적으로 uid 0 을 굽는다).
    assert db.query("SELECT request_id FROM requests") == []


def test_token_actor_node_form_is_accepted(db):
    # 에이전트 무손상: node:<이름> 은 그대로 통과해 actor 로 실린다(설계 §2.2-1).
    client = _client(db)
    r = client.get("/api/auth/me",
                   headers={"Authorization": "Bearer tok-shared",
                            "x-dms-actor": "node:n1"})
    assert r.status_code == 200
    assert r.json() == {"actor": "node:n1", "role": "admin"}


def test_token_actor_empty_normalizes_to_shared_token(db):
    # 빈 값/공백만은 400 이 아니라 shared-token 정규화(옆문 유지 금지, 설계 §2.2-1).
    client = _client(db)
    for value in (None, "", "   "):
        headers = {"Authorization": "Bearer tok-shared"}
        if value is not None:
            headers["x-dms-actor"] = value
        assert client.get("/api/auth/me", headers=headers).json() == {
            "actor": "shared-token", "role": "admin"}


def test_node_regex_has_single_source_of_truth():
    # actor 게이트가 통과시킨 node:<이름> 을 에이전트 라우트가 다시 검증한다 --
    # 두 규칙이 갈라지면 한쪽이 통과시킨 값을 다른 쪽이 403 으로 거절한다. 같은
    # 컴파일된 객체를 재사용해 갈라짐 자체를 불가능하게 한다.
    assert auth_mod._NODE_NAME_RE is agent_mod._NODE_NAME_RE
```

- [ ] **Step 2: 실패(=구멍이 살아 있음)를 확인한다**

Run:
```
PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_actor_spoof_regression.py -q
```
Expected: FAIL 3건, PASS 1건.
- `test_privileged_requester_synthesizes_root_uid_zero` **PASS** — 승격 메커니즘은 이미 존재한다(구멍의 결과가 진짜임을 증명).
- `test_shared_token_cannot_choose_privileged_actor` **FAIL** — `assert 400 == ...` 이전에 `r.status_code` 가 202 다. 정확한 실패: `AssertionError: assert 202 == 400`. 이것이 **스푸핑이 오늘 열려 있다는 증거**(서버가 `x-dms-actor: root` 를 그대로 받아 requester_id=root 요청을 만들었다).
- `test_token_actor_empty_normalizes_to_shared_token` **PASS** — 현행도 빈값→shared-token.
- `test_node_regex_has_single_source_of_truth` **FAIL** — `AttributeError: module 'dms.api.auth' has no attribute '_NODE_NAME_RE'`.
- `test_token_actor_node_form_is_accepted` **PASS** — 현행도 임의 actor 를 그대로 받으므로 `node:n1` 도 통과(게이트 후에도 통과해야 하는 회귀 지점).

- [ ] **Step 3: `_NODE_NAME_RE` 를 auth.py 로 승격한다**

`src/dms/api/auth.py` — 파일 상단 import 에 `import re` 를 더하고(현재 `import hmac` 만), `Identity` 정의(:20) **위**에 정규식을 둔다:

```python
import hmac
import re
from collections import namedtuple
from fastapi import HTTPException, Request
```

`_RESERVED_ACTOR_PREFIX = "token:"`(:8) 아래에 추가:

```python
# 에이전트 노드 이름 규칙(DNS-1123). routes_agent 의 잡 신원 검증(ingest_report)이
# 쓰는 바로 그 규칙이어야 한다 -- 토큰 경로에서 여기서 통과시킨 node:<이름> 을
# 에이전트 라우트가 다시 검증하므로, 두 곳이 갈라지면 한쪽이 받은 값을 다른 쪽이
# 거절한다. 그래서 정의는 여기 한 곳뿐이고 routes_agent 가 이 상수를 import 한다.
_NODE_NAME_RE = re.compile(r"[A-Za-z0-9]([A-Za-z0-9.-]{0,252}[A-Za-z0-9])?$")
```

`src/dms/api/routes_agent.py` — `import re`(:1)를 지우고, `_NODE_NAME_RE` 로컬 정의(:9)를 지우고, auth import 를 확장한다:

```python
from fastapi import APIRouter, Depends, HTTPException, Request

from .auth import Identity, _NODE_NAME_RE, require_user
```

(파일 나머지 — `_NODE_NAME_RE.fullmatch(node_name)`(구 :16) 사용부 — 는 그대로 둔다. import 된 상수를 쓴다.)

- [ ] **Step 4: 토큰 경로 actor 게이트를 넣는다**

`src/dms/api/auth.py` — `current_identity` 의 토큰 분기(:46-49)를 교체한다. 기존:

```python
            actor = (request.headers.get("x-dms-actor") or "").strip() or "shared-token"
            if actor.startswith(_RESERVED_ACTOR_PREFIX):
                raise HTTPException(status_code=401, detail="invalid_actor")
            return Identity(actor=actor, role="admin", auth="token")
```

를 다음으로:

```python
            # 슬라이스 19: 토큰 경로의 x-dms-actor 는 node:<이름>(에이전트 전용)만
            # 허용한다. 빈 값/공백만은 기존대로 shared-token 으로 정규화하고(옆문 유지
            # 금지 -- 빈 "token:" 감사 위장 방지), 그 외 임의 값(root, alice,
            # token:x ...)은 400 으로 거절한다. 이 한 줄이 공유 토큰 보유자가 잡 신원을
            # 자유 지정해 uid 0 으로 승격하던 스푸핑(설계 §1-4)을 닫는다: requester_id
            # 가 사람 이름/특권 이름이 될 여지가 사라진다. token: 접두 거절(구 401)은
            # 더 넓은 이 게이트에 흡수된다(node: 아님 -> 400).
            raw = (request.headers.get("x-dms-actor") or "").strip()
            if not raw:
                actor = "shared-token"
            elif raw.startswith("node:") and _NODE_NAME_RE.fullmatch(raw[len("node:"):]):
                actor = raw
            else:
                raise HTTPException(status_code=400, detail="invalid_actor")
            return Identity(actor=actor, role="admin", auth="token")
```

(`_RESERVED_ACTOR_PREFIX` 상수와 `audit_actor`(:23-27)는 그대로 둔다 — 헤더 검증이 아니라 감사 로그 표시용으로 계속 쓰인다.)

- [ ] **Step 5: 스푸핑 테스트가 뒤집힘을 확인한다**

Run:
```
PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_actor_spoof_regression.py -q
```
Expected: **5건 전부 PASS**. 특히 `test_shared_token_cannot_choose_privileged_actor` 가 400 + 요청 미생성으로 뒤집혔다.

- [ ] **Step 6: 게이트가 깨뜨린 21개 테스트 파일을 정리한다**

각 파일에서 `ADMIN`(또는 인라인 `admin`) 상수의 `x-dms-actor` 키를 **지운다**. `x-dms-actor: "ops"` 가 없으면 actor 는 `shared-token` 으로 정규화돼 토큰만으로 admin 이 유지된다(설계 §2.2-4). 다음 16개 파일에서 정확히 같은 치환:

`ADMIN = {"Authorization": "Bearer tok-shared", "x-dms-actor": "ops"}` → `ADMIN = {"Authorization": "Bearer tok-shared"}`

- `tests/test_api_admin_accounts.py:1`
- `tests/test_api_agent.py:8`
- `tests/test_api_artifacts.py:44`
- `tests/test_api_builds.py:1`
- `tests/test_api_control.py:1`
- `tests/test_api_denylist.py:1`
- `tests/test_api_jobs.py:118`
- `tests/test_api_maintenance.py:1`
- `tests/test_api_metrics.py:4`
- `tests/test_api_nodes.py:1`
- `tests/test_api_policies.py:3`
- `tests/test_api_releases.py:5`
- `tests/test_api_request_cancel.py:3`
- `tests/test_api_requests.py:1`
- `tests/test_api_scan_paths.py:1`
- `tests/test_api_user_storages.py:1`
- `tests/test_auth_session_recheck.py:1`
- `tests/test_default_priority.py:11`

`tests/test_api_scan_path_stats.py` — 인라인 `admin` 두 곳(`:78`, `:346`):

`admin = {"Authorization": "Bearer tok-shared", "x-dms-actor": "ops"}` → `admin = {"Authorization": "Bearer tok-shared"}`

`tests/test_agent_runner.py` — **변경 없음**. `x-dms-actor`(:36)는 러너가 보내는 `node:<이름>` 을 읽는 단언이라 게이트에 걸리지 않는다.

`tests/test_api_agent.py`·`tests/test_api_nodes.py` — 위 ADMIN 치환만. 에이전트 헤더(`f"node:{node}"`)와 bad-node 테스트(`test_api_agent.py:54` `x-dms-actor: f"node:{bad}"`)는 그대로 둔다.

- [ ] **Step 7: actor 를 단언하던 곳은 새 값으로 갱신한다(단언을 지우지 않는다)**

감사 표식이 계속 기록되는지가 이 변경의 회귀 지점이다. 헤더를 지운 뒤 토큰 actor 는 `shared-token` 이므로 `audit_actor` 접두는 `token:shared-token` 이 된다.

`tests/test_api_releases.py:231` — 기존:
```python
    assert entry["actor"] == "token:ops"
```
→
```python
    # 헤더에서 x-dms-actor 를 지운 뒤 토큰 actor 는 shared-token 으로 정규화되고
    # audit_actor 가 token: 접두를 붙인다(감사 표식은 계속 기록된다).
    assert entry["actor"] == "token:shared-token"
```

`tests/test_api_control.py:74` — 기존 `assert rows[-1]["actor"] == "token:ops"` →
```python
    assert rows[-1]["actor"] == "token:shared-token"
```

`tests/test_api_auth.py` — `test_shared_token_grants_admin`(:17-21)과 `test_auth_me_returns_raw_actor_for_token_auth`(:71-77)이 `x-dms-actor: "ops-debug"` 를 보낸다. `ops-debug` 는 `node:` 접두가 없어 새 게이트에서 400 이 되므로 **헤더를 제거**하고 기대 actor 를 `shared-token` 으로 갱신한다.

`test_shared_token_grants_admin`(:17-21) 교체:
```python
def test_shared_token_grants_admin(client):
    # x-dms-actor 없이도 토큰만으로 admin. actor 는 shared-token 으로 정규화된다.
    r = client.get("/api/auth/me", headers={"Authorization": "Bearer tok-shared"})
    assert r.status_code == 200
    assert r.json() == {"actor": "shared-token", "role": "admin"}
```

`test_auth_me_returns_raw_actor_for_token_auth`(:71-77) 교체:
```python
def test_auth_me_returns_raw_actor_for_token_auth(client):
    # /api/auth/me 는 감사 로그가 아니라 로그인 신원 표시용이다 -- audit_actor 의
    # token: 접두가 여기 새어 들어가면 프론트 헤더와 기존 단언이 깨진다. 기본 토큰
    # actor 는 shared-token(콜론 없음)이다.
    r = client.get("/api/auth/me", headers={"Authorization": "Bearer tok-shared"})
    assert r.json()["actor"] == "shared-token"
    assert ":" not in r.json()["actor"]
```

`test_reserved_prefix_in_actor_header_is_rejected`(:85-90) — `token:alice` 는 `node:` 아님 → 새 게이트에서 **400** 으로 흡수된다(설계 §2.2-4). 상태·detail 갱신:
```python
def test_reserved_prefix_in_actor_header_is_rejected(client):
    # token:alice 는 node:<이름> 이 아니라 더 넓은 actor 게이트에 흡수돼 400 이다
    # (슬라이스 19). 접두 위조로 감사 로그를 오염시키던 경로는 여전히 닫혀 있다.
    r = client.get("/api/auth/me", headers={
        "Authorization": "Bearer tok-shared", "x-dms-actor": "token:alice"})
    assert r.status_code == 400
    assert r.json()["detail"] == "invalid_actor"
```

`tests/test_api_control.py` — `test_reserved_actor_prefix_in_header_is_rejected`(:99-105)도 `token:alice` → 401 을 단언한다. 400 으로 갱신:
```python
def test_reserved_actor_prefix_in_header_is_rejected(client):
    # token:alice 는 node:<이름> 이 아니므로 actor 게이트가 400 으로 거절한다(슬라이스 19).
    r = client.put("/api/admin/control-state",
                   headers={"Authorization": "Bearer tok-shared",
                            "x-dms-actor": "token:alice"},
                   json={"maintenance": False, "drain": False, "reason": None})
    assert r.status_code == 400
```

`tests/test_api_control.py` 의 빈값/공백 테스트(`:108-127`, `test_empty_actor_header_audit_is_not_bare_prefix`·`test_whitespace_only_actor_header_audit_is_not_bare_prefix`)와 기본 actor 테스트(`:77-84`, `test_token_auth_default_actor_audit_is_not_bare_prefix`)는 **그대로 둔다** — 빈값/공백은 여전히 200 + `token:shared-token`(설계 §2.2-1, 게이트가 이 경로를 바꾸지 않는다).

- [ ] **Step 8: 통과를 확인한다**

Run:
```
PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_actor_spoof_regression.py tests/test_api_auth.py tests/test_api_control.py tests/test_api_releases.py tests/test_api_agent.py tests/test_api_admin_accounts.py -q
```
Expected: 전부 PASS. 이어서 전체 스위트로 21개 파일 정리 누락을 잡는다:
```
PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest -q
```
Expected: 전부 PASS(기준선 998 + 신규 5). 어느 파일이든 `x-dms-actor: "ops"` 를 남겼으면 그 파일의 admin 호출이 400 으로 무더기 실패하므로 여기서 드러난다.

- [ ] **Step 9: 커밋**

```bash
git add src/dms/api/auth.py src/dms/api/routes_agent.py tests/test_actor_spoof_regression.py \
  tests/test_api_admin_accounts.py tests/test_api_agent.py tests/test_api_artifacts.py \
  tests/test_api_auth.py tests/test_api_builds.py tests/test_api_control.py \
  tests/test_api_denylist.py tests/test_api_jobs.py tests/test_api_maintenance.py \
  tests/test_api_metrics.py tests/test_api_nodes.py tests/test_api_policies.py \
  tests/test_api_releases.py tests/test_api_request_cancel.py tests/test_api_requests.py \
  tests/test_api_scan_paths.py tests/test_api_scan_path_stats.py tests/test_api_user_storages.py \
  tests/test_auth_session_recheck.py tests/test_default_priority.py
git commit -m "fix(auth): 토큰 actor 스푸핑 차단 — x-dms-actor 를 node:<이름> 으로 좁혀 uid 0 승격 봉쇄(기존 21파일 정리)"
```

---

### Task 2: 특권 승격을 세션 인증 요청 전용으로 (심층 방어)

Task 1 이 requester_id 가 특권 이름이 될 여지를 이미 막지만, `resolve_job_identity` 의 승격을 `auth='session'` 요청에만 허용해 다른 경로로 특권 이름이 들어올 여지까지 닫는다(설계 §2.2-2). 요청은 자신의 인증 방식을 모르므로 `requests.auth_method` 컬럼에 저장하고, 유일 호출자 `planner.py` 가 그 값을 `resolve_job_identity` 로 넘긴다.

**Files:**
- Modify: `src/dms/migrations.py`, `src/dms/repositories/requests.py`, `src/dms/identity.py`, `src/dms/planner.py`, `src/dms/api/routes_requests.py`, `src/dms/batch_orchestrator.py`
- Test: `tests/test_identity.py`, `tests/test_planner.py`, `tests/test_migrations.py`, `tests/test_api_requests_privileged.py`(무변경 회귀 확인)

**Interfaces:**
- Consumes: `Identity.auth`(`auth.py`), `TERMINAL_REQUEST_STATES`(`domain.py`).
- Produces:
  - `requests.auth_method` 컬럼(TEXT, 기본 없음). `RequestsRepository.create(..., auth_method="token")` — **기본 "token"**(비특권 안전 기본).
  - `resolve_job_identity(..., session_authenticated: bool = True)` — `privileged = allow_privileged and session_authenticated and requester_id in privileged_requesters`.
  - planner 가 `session_authenticated=(req.get("auth_method") == "session")` 로 계산해 전달.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_identity.py` 파일 끝에 추가:

```python
def test_privileged_requires_session_auth(db):
    # 심층 방어(설계 §2.2-2): 같은 특권 requester 라도 session 이면 root, token 이면
    # 특권을 강제로 끈다. token 경로는 privileged 를 못 얻어 LDAP 경로로 떨어진다.
    control = _control(db)
    out = resolve_job_identity(control, None, requester_id="ops",
                              owner_username="victim", allow_privileged=True,
                              privileged_requesters=frozenset({"ops"}),
                              session_authenticated=True)
    assert out.privileged and out.uid == 0
    with pytest.raises(IdentityRejected) as e:
        resolve_job_identity(control, None, requester_id="ops",
                            owner_username="victim", allow_privileged=True,
                            privileged_requesters=frozenset({"ops"}),
                            session_authenticated=False)
    # 특권을 안 쓰므로 resolver=None 인 LDAP 경로로 떨어진다.
    assert e.value.reason_code == "ldap_not_configured"
```

`tests/test_planner.py` 파일 끝에 추가(기존 `_seed_storage`/`_seed_policy`/`_seed_report`/`Repositories`/`ResolvedIdentity`/`StubIdentityResolver`/`Planner`/`NOW` 를 재사용):

```python
class _PrivSettings:
    agent_report_stale_seconds = 300
    allow_privileged_requesters = True
    privileged_requesters = frozenset({"root"})
    planner_identity_grace_seconds = 300


def _root_request(repos, key, auth_method):
    return repos.requests.create(
        operation="scan", requester_id="root", actor="root", resource_key=key,
        payload={"storage": "s1", "target": "a", "options": {},
                 "owner_username": None},
        priority="mid", auth_method=auth_method)


def test_session_auth_root_request_runs_privileged(db):
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos); _seed_report(repos, user="root")
    rid = _root_request(repos, "k-session", "session")
    resolver = StubIdentityResolver({"root": ResolvedIdentity("root", 5000, 5000, (), False)})
    Planner(repos, resolver, settings=_PrivSettings()).run_once(now_iso=NOW)
    ident = repos.data_jobs.list_jobs(request_id=rid)[0]["worker_pool"]["identity"]
    assert ident["uid"] == 0 and ident["privileged"] is True


def test_token_auth_root_request_never_runs_privileged(db):
    # 설계 §2.2-2 심층 방어: 토큰 인증이면 requester_id 가 root 라도 uid 0 이 아니라
    # LDAP 로 해석된 실제 uid 로 돈다. (Task 1 이 애초에 token 으로 requester_id=root
    # 를 못 만들게 막지만, 다른 경로로 들어와도 여기서 다시 끊긴다.)
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos); _seed_report(repos, user="root")
    rid = _root_request(repos, "k-token", "token")
    resolver = StubIdentityResolver({"root": ResolvedIdentity("root", 5000, 5000, (), False)})
    Planner(repos, resolver, settings=_PrivSettings()).run_once(now_iso=NOW)
    ident = repos.data_jobs.list_jobs(request_id=rid)[0]["worker_pool"]["identity"]
    assert ident["uid"] == 5000 and ident["privileged"] is False
```

`tests/test_migrations.py` 파일 끝에 추가:

```python
def test_migrate_adds_auth_method_to_existing_requests(db):
    # 구형 requests 를 흉내: auth_method 컬럼을 빼고 재생성. 한쪽(CREATE)만 넣으면
    # 기배포 DB 는 이 컬럼이 없어 planner 의 req["auth_method"] 조회가 라이브에서만
    # 터진다(슬라이스 14 의 실 500 교훈).
    db.execute("DROP TABLE requests")
    db.execute("""CREATE TABLE requests (request_id TEXT PRIMARY KEY,
        commit_order INTEGER NOT NULL UNIQUE, operation TEXT NOT NULL,
        requester_id TEXT NOT NULL, actor TEXT NOT NULL, resource_key TEXT NOT NULL,
        priority TEXT NOT NULL DEFAULT 'mid', payload TEXT NOT NULL, state TEXT NOT NULL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL, batch_id TEXT)""")
    from dms.migrations import migrate, _column_exists
    migrate(db)
    assert _column_exists(db, "requests", "auth_method")
```

- [ ] **Step 2: 실패를 확인한다**

Run:
```
PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_identity.py tests/test_planner.py tests/test_migrations.py -q
```
Expected: FAIL —
- `test_privileged_requires_session_auth`: `TypeError: resolve_job_identity() got an unexpected keyword argument 'session_authenticated'`.
- `test_session_auth_root_request_runs_privileged`·`test_token_auth_root_request_never_runs_privileged`: `TypeError: create() got an unexpected keyword argument 'auth_method'`.
- `test_migrate_adds_auth_method_to_existing_requests`: `AssertionError` (`_column_exists(... "auth_method")` False).

- [ ] **Step 3: 마이그레이션에 auth_method 컬럼을 넣는다(양쪽)**

`src/dms/migrations.py` — CREATE TABLE requests(:55-67)의 `batch_id TEXT)` 를 다음으로 교체:

```python
            batch_id TEXT,
            -- 슬라이스 19: 요청을 만든 인증 방식(session/token). planner 가 특권 승격을
            -- session 요청에만 허용하는 심층 방어에 쓴다(설계 §2.2-2). 기존 행/기본은
            -- token(비특권) -- 승격은 명시적으로 session 일 때만.
            auth_method TEXT)""",
```

`_ensure_columns` 튜플(:406-425) — `("requests", "batch_id", "TEXT"),`(:418) 아래에 추가:

```python
        # 슬라이스 19: 기배포 DB 는 CREATE 를 다시 안 탄다 -- 양쪽에 넣지 않으면
        # planner 의 req["auth_method"] 가 라이브에서만 없다(슬라이스 14 교훈).
        ("requests", "auth_method", "TEXT"),
```

- [ ] **Step 4: requests.create 에 auth_method 를 넣고 has_active_for_requester 를 더한다**

`src/dms/repositories/requests.py` — `create`(:21-40) 시그니처와 INSERT 를 확장한다. 시그니처(:21-22):

```python
    def create(self, *, operation, requester_id, actor, resource_key,
               payload: dict, priority: str, batch_id=None,
               auth_method="token") -> str:
```

INSERT 문(:28-38)의 컬럼 목록·VALUES·바인딩에 auth_method 를 추가:

```python
            self._db.execute(
                """INSERT INTO requests (request_id, commit_order, operation, requester_id,
                       actor, resource_key, priority, payload, state, created_at, updated_at,
                       batch_id, auth_method)
                   VALUES (:id, :o, :op, :req, :actor, :key, :pri, :payload, :state, :now, :now,
                       :bid, :auth)""",
                {"id": request_id, "o": order, "op": operation, "req": requester_id,
                 "actor": actor, "key": resource_key, "pri": priority,
                 "payload": dump_json(payload), "state": RequestState.PENDING.value,
                 "now": now, "bid": batch_id, "auth": auth_method},
            )
```

파일 끝(`finalize_from_job` 뒤)에 조회 메서드를 추가(설계 §2.3 안전장치 3, `active_referencing_storage`(:102-113)와 같은 NOT-IN 패턴):

```python
    def has_active_for_requester(self, requester_id) -> bool:
        """이 requester 소유의 비종단(진행 중) 요청이 하나라도 있으면 True. 잡 신원은
        plan 시점에 구워져(설계 §1-6) 삭제가 소급되지 않으므로, 소유자 삭제 전에
        진행 중 요청을 막아 '소유자 없는 잡'을 예방한다. TERMINAL_REQUEST_STATES 밖의
        상태를 NOT IN 으로 센다(find_active 와 같은 placeholder 관례)."""
        terminal = tuple(s.value for s in TERMINAL_REQUEST_STATES)
        placeholders = ", ".join(f":t{i}" for i in range(len(terminal)))
        params = {f"t{i}": v for i, v in enumerate(terminal)}
        params["req"] = requester_id
        row = self._db.query_one(
            f"""SELECT 1 AS x FROM requests
                WHERE requester_id = :req AND state NOT IN ({placeholders})
                LIMIT 1""", params)
        return row is not None
```

- [ ] **Step 5: resolve_job_identity 에 session_authenticated 파라미터를 넣는다**

`src/dms/identity.py` — `resolve_job_identity` 시그니처(:42-43)를 교체:

```python
def resolve_job_identity(control, resolver, *, requester_id, owner_username,
                         allow_privileged, privileged_requesters,
                         session_authenticated: bool = True) -> ResolvedIdentity:
```

특권 판정(:49)을 교체:

```python
    # 특권 승격은 session 인증 요청에만 허용한다(슬라이스 19 심층 방어, 설계 §2.2-2):
    # 공유 토큰 경로는 requester_id 를 자유 지정할 수 없게 이미 좁혔지만(Task 1),
    # 토큰으로 들어온 요청은 여기서도 특권을 못 얻는다. 기본값 True 는 이 함수의 유일
    # 프로덕션 호출자(planner)가 요청의 auth_method 로 실제 값을 넘기기 때문에 정상
    # 경로에선 쓰이지 않는다 -- 직접 호출하는 단위 테스트의 편의를 위한 기본이다.
    privileged = (allow_privileged and session_authenticated
                  and requester_id in privileged_requesters)
```

- [ ] **Step 6: planner·routes_requests·batch_orchestrator 를 배선한다**

`src/dms/planner.py` — identity 해석(:152-158)에 `session_authenticated` 를 넘긴다. `resolve_job_identity(...)` 호출을 교체:

```python
            identity = resolve_job_identity(
                self._repos.control, self._resolver,
                requester_id=req["requester_id"],
                owner_username=payload.get("owner_username"),
                allow_privileged=self._settings.allow_privileged_requesters,
                privileged_requesters=self._settings.privileged_requesters,
                # 요청을 만든 인증 방식. token(또는 컬럼 미채움)은 특권을 못 얻는다.
                session_authenticated=(req.get("auth_method") == "session"))
```

`src/dms/api/routes_requests.py` — `submit`(:75-105)의 `repos.requests.create(...)`(:102-104)에 `auth_method` 를 넘긴다:

```python
    rid = repos.requests.create(
        operation=body.operation, requester_id=identity.actor, actor=identity.actor,
        resource_key=resource_key, payload=payload, priority=priority,
        # 특권 승격을 session 요청에만 허용하기 위해 인증 방식을 요청에 실어 둔다
        # (planner 가 plan 시점에 읽는다, 설계 §2.2-2).
        auth_method=identity.auth)
```

`src/dms/batch_orchestrator.py` — `_materialize`(:62-70)의 `requests.create(...)`(:66-69)에 `auth_method="token"` 을 넘긴다:

```python
        rid = self._repos.requests.create(
            operation=batch["operation"], requester_id=batch["requester_id"],
            actor=batch["actor"], resource_key=key, payload=payload,
            priority=priority, batch_id=batch["batch_id"],
            # 배치 자식은 기계가 materialize 하는 것이지 사람이 세션으로 낸 것이 아니다
            # -- 특권 없이 실 신원(LDAP)로만 돈다(설계 §2.2-2, 비특권 안전 기본).
            auth_method="token")
```

- [ ] **Step 7: 통과를 확인한다**

Run:
```
PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_identity.py tests/test_planner.py tests/test_migrations.py tests/test_api_requests_privileged.py tests/test_repo_requests.py tests/test_repo_requests_finalize.py -q
```
Expected: 전부 PASS. `test_api_requests_privileged.py` 는 세션 로그인 경로라 `auth_method="session"` 으로 특권이 계속 열려 `test_admin_operator_with_flag_can_submit_for_other` 가 202 유지(설계 §2.2-2 회귀 지점). 기존 `resolve_job_identity` 단위 테스트들은 `session_authenticated` 기본 True 로 무영향.

- [ ] **Step 8: 커밋**

```bash
git add src/dms/migrations.py src/dms/repositories/requests.py src/dms/identity.py \
  src/dms/planner.py src/dms/api/routes_requests.py src/dms/batch_orchestrator.py \
  tests/test_identity.py tests/test_planner.py tests/test_migrations.py
git commit -m "fix(identity): 특권 승격을 session 인증 요청 전용으로 — requests.auth_method 컬럼으로 심층 방어"
```

---

### Task 3: 계정 하드 삭제 리포지토리 + 마지막 활성 관리자 카운트

`accounts` 삭제 + `user_scan_paths` 연쇄 정리 + 감사를 한 트랜잭션으로 하는 `delete` 와, 삭제·강등·비활성화 세 라우트가 공유할 `active_admin_count` 를 리포지토리에 더한다. 라우트는 Task 4·5.

**Files:**
- Modify: `src/dms/repositories/accounts.py`
- Test: `tests/test_repo_accounts_admin.py`

**Interfaces:**
- Consumes: `_audit_account(operation, username, before, after, actor, now)`(:76-82), `get(username)`(:65-69, password_hash 제외), `ROLE_ADMIN`(이미 import, :6), `with self._db.transaction()`.
- Produces:
  - `AccountsRepository.delete(username, *, actor)` — accounts + user_scan_paths + 감사(`operation='delete'`, `before_state` = 계정 스냅샷, `after_state` = null) 한 트랜잭션. 대상 부재 시 `KeyError`.
  - `AccountsRepository.active_admin_count() -> int` — `role='admin' AND disabled=0` 인 계정 수.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_repo_accounts_admin.py` 파일 끝에 추가(기존 `_mk`·`Repositories` 재사용):

```python
def test_delete_removes_account_scan_paths_and_audits(db):
    repos = Repositories(db)
    _mk(repos, name="doomed", role="user")
    repos.user_scan_paths.add("doomed", "ceph-a", "team")
    repos.user_scan_paths.add("other", "ceph-a", "keep")   # 타인 행은 남는다
    repos.accounts.delete("doomed", actor="ops")
    assert repos.accounts.get("doomed") is None
    assert repos.user_scan_paths.list_for("doomed") == []
    # 타인의 scan 경로는 연쇄되지 않는다 -- 삭제 대상 소유 리소스만 정리한다.
    assert len(repos.user_scan_paths.list_for("other")) == 1
    rows = db.query(
        "SELECT * FROM audit_log WHERE mutation_class='account' AND operation='delete'")
    assert len(rows) == 1
    assert rows[0]["target_key"] == "doomed" and rows[0]["actor"] == "ops"
    # before_state 스냅샷은 존재하고 after_state 는 null(하드 삭제), password_hash 없음.
    assert rows[0]["before_state"] is not None
    assert "password_hash" not in rows[0]["before_state"]
    assert rows[0]["after_state"] in (None, "null")


def test_delete_missing_account_raises(db):
    repos = Repositories(db)
    with pytest.raises(KeyError):
        repos.accounts.delete("ghost", actor="ops")


def test_active_admin_count_ignores_disabled_and_users(db):
    repos = Repositories(db)
    _mk(repos, name="a1", role="admin")
    _mk(repos, name="a2", role="admin")
    _mk(repos, name="u1", role="user")
    repos.accounts.set_disabled("a2", True, actor="ops")   # 비활성 admin 은 제외
    assert repos.accounts.active_admin_count() == 1
```

- [ ] **Step 2: 실패를 확인한다**

Run:
```
PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_repo_accounts_admin.py -q
```
Expected: FAIL — `AttributeError: 'AccountsRepository' object has no attribute 'delete'`(및 `active_admin_count`).

- [ ] **Step 3: delete·active_admin_count 를 구현한다**

`src/dms/repositories/accounts.py` — `set_disabled`(:96-104) 아래(클래스 끝)에 추가:

```python
    def active_admin_count(self) -> int:
        """활성 관리자(role='admin' AND disabled=0) 수. 삭제·강등·비활성화 세 경로가
        '마지막 활성 관리자'를 잠그지 못하게 하는 데 쓴다(설계 §2.3 안전장치 2).
        공유 토큰이 항상 admin 이라 완전 잠금은 아니지만 사람 admin 0 명은 사고다."""
        row = self._db.query_one(
            "SELECT COUNT(*) AS c FROM accounts WHERE role = :r AND disabled = 0",
            {"r": ROLE_ADMIN})
        return row["c"]

    def delete(self, username, *, actor):
        """하드 삭제(설계 §2.3): accounts + user_scan_paths(계정 소유 리소스) + 감사를
        한 트랜잭션으로 묶는다 -- 부분 삭제나 감사 누락을 막는다(set_role 이 이미 쓰는
        transaction 관례). FK 가 저장소 전체에 0 건이라(설계 §1-7) requests/audit_log 의
        문자열 actor 는 그대로 남는다 -- 버그가 아니라 이력 보존이다. before_state
        스냅샷은 get()이 password_hash 를 SELECT 에서 빼므로 자연히 해시가 빠진다."""
        with self._db.transaction():
            before = self.get(username)
            if before is None:
                raise KeyError(username)
            self._db.execute("DELETE FROM accounts WHERE username = :u", {"u": username})
            # user_scan_paths 는 username 을 관례로만 참조한다(제약 없음, §1-7). 소유자가
            # 사라지면 아무도 볼 수 없는 데드 로우가 되므로 여기서 함께 지운다.
            self._db.execute("DELETE FROM user_scan_paths WHERE username = :u",
                             {"u": username})
            self._audit_account("delete", username, before, None, actor, utc_now_iso())
```

- [ ] **Step 4: 통과를 확인한다**

Run:
```
PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_repo_accounts_admin.py tests/test_repo_accounts.py tests/test_repo_scan_paths.py -q
```
Expected: 전부 PASS.

- [ ] **Step 5: 커밋**

```bash
git add src/dms/repositories/accounts.py tests/test_repo_accounts_admin.py
git commit -m "feat(accounts): 하드 삭제 리포지토리 — accounts+user_scan_paths+감사 한 트랜잭션 + 활성 관리자 카운트"
```

---

### Task 4: DELETE 라우트 + 안전장치 3종

`DELETE /api/admin/accounts/{username}` 를 더하고 존재 확인 → 자기 삭제 → 마지막 활성 관리자 → 비종단 요청 순으로 게이트한다(설계 §4).

**Files:**
- Modify: `src/dms/api/routes_accounts.py`
- Test: `tests/test_api_admin_accounts.py`

**Interfaces:**
- Consumes: `accounts.get`/`accounts.delete`/`accounts.active_admin_count`(Task 3), `requests.has_active_for_requester`(Task 2), `audit_actor`(`auth.py`), `require_admin`.
- Produces: `DELETE /api/admin/accounts/{username}` → 204(성공). 오류: 404 `account_not_found`, 409 `cannot_delete_self`, 409 `last_active_admin`, 409 `account_has_active_requests`. 공유 헬퍼 `_guard_last_active_admin(repos, account)`.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_api_admin_accounts.py` 파일 끝에 추가(기존 `ADMIN`(Task 1 정리 후 `{"Authorization": "Bearer tok-shared"}`)·`_login` 재사용):

```python
def _mk_admin(client, name):
    # x-admin-token 부트스트랩은 계정을 곧바로 ROLE_ADMIN 으로 만든다
    # (routes_auth.create_admin_account -> accounts.create(..., ROLE_ADMIN)).
    client.post("/api/admin/accounts", json={"username": name, "password": "p"},
                headers={"x-admin-token": "tok-admin"})


def test_delete_account_removes_it_and_audits(client, db):
    client.post("/api/auth/signup", json={"username": "victim", "password": "p"})
    assert client.delete("/api/admin/accounts/victim", headers=ADMIN).status_code == 204
    listed = client.get("/api/admin/accounts", headers=ADMIN).json()
    assert all(r["username"] != "victim" for r in listed)   # 목록에서 사라졌다
    rows = db.query(
        "SELECT * FROM audit_log WHERE mutation_class='account' AND operation='delete'")
    assert len(rows) == 1 and rows[0]["target_key"] == "victim"
    # 토큰 호출이므로 감사 actor 는 token: 접두(감사 표식 회귀 지점).
    assert rows[0]["actor"] == "token:shared-token"


def test_delete_missing_account_404(client):
    r = client.delete("/api/admin/accounts/nope", headers=ADMIN)
    assert r.status_code == 404 and r.json()["detail"] == "account_not_found"


def test_delete_self_forbidden(client):
    # 세션으로 로그인한 관리자가 자기 자신을 삭제하려 하면 409, 상태 불변. self-guard 가
    # 마지막 관리자 가드보다 먼저이므로 selfadm 이 유일 admin 이어도 cannot_delete_self.
    _mk_admin(client, "selfadm")
    _login(client, "selfadm")
    r = client.delete("/api/admin/accounts/selfadm")
    assert r.status_code == 409 and r.json()["detail"] == "cannot_delete_self"
    listed = client.get("/api/admin/accounts", headers=ADMIN).json()
    assert any(row["username"] == "selfadm" for row in listed)


def test_delete_last_active_admin_forbidden(client):
    # 유일한 사람 admin 을 토큰(shared-token, actor 불일치라 self-guard 미발동)으로
    # 삭제 시도 -> 409, 계정 불변.
    _mk_admin(client, "onlyadm")
    r = client.delete("/api/admin/accounts/onlyadm", headers=ADMIN)
    assert r.status_code == 409 and r.json()["detail"] == "last_active_admin"
    listed = client.get("/api/admin/accounts", headers=ADMIN).json()
    assert any(row["username"] == "onlyadm" for row in listed)


def test_delete_one_of_two_admins_succeeds(client):
    # 대조: admin 이 둘이면 한 명 삭제는 통과한다(마지막 관리자 가드는 '마지막'만 막는다).
    _mk_admin(client, "adm1")
    _mk_admin(client, "adm2")
    assert client.delete("/api/admin/accounts/adm2", headers=ADMIN).status_code == 204


def test_delete_account_with_active_request_forbidden(client, db):
    # 비종단 요청을 가진 계정 삭제는 409 -- 잡 신원은 plan 시점에 구워져 삭제가
    # 소급되지 않으므로(설계 §1-6) 소유자 없는 잡을 예방한다.
    client.post("/api/auth/signup", json={"username": "busy", "password": "p"})
    db.execute(
        """INSERT INTO requests (request_id, commit_order, operation, requester_id, actor,
               resource_key, priority, payload, state, created_at, updated_at, auth_method)
           VALUES ('rq1', 1, 'scan', 'busy', 'busy', 'k', 'mid', '{}', 'Pending',
               '2026-08-10T00:00:00Z', '2026-08-10T00:00:00Z', 'session')""")
    r = client.delete("/api/admin/accounts/busy", headers=ADMIN)
    assert r.status_code == 409 and r.json()["detail"] == "account_has_active_requests"
    assert client.get("/api/admin/accounts", headers=ADMIN).json()   # busy 여전히 존재
```

- [ ] **Step 2: 실패를 확인한다**

Run:
```
PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_api_admin_accounts.py -q
```
Expected: FAIL — 새 6건이 `405 Method Not Allowed`(DELETE 라우트 부재)로 상태 코드 단언에서 깨진다. 기존 테스트는 PASS.

- [ ] **Step 3: DELETE 라우트와 마지막 관리자 헬퍼를 구현한다**

`src/dms/api/routes_accounts.py` — import(:1-4)에 `ROLE_ADMIN` 을 더한다:

```python
from ..domain import DomainValidationError, ROLE_ADMIN
```

`_guard_self`(:17-20) 아래에 헬퍼를 더한다:

```python
def _guard_last_active_admin(repos, account: dict) -> None:
    # 활성 관리자를 0명으로 만드는 변경(삭제·강등·비활성화)을 막는다. 대상이 지금
    # 활성 관리자이고 그 수가 1이면(=대상이 마지막) 409(설계 §2.3 안전장치 2).
    if (account["role"] == ROLE_ADMIN and account["disabled"] == 0
            and repos.accounts.active_admin_count() <= 1):
        raise HTTPException(status_code=409, detail="last_active_admin")
```

`set_disabled` 라우트(:46-57) 아래(파일 끝)에 DELETE 라우트를 더한다:

```python
@router.delete("/api/admin/accounts/{username}", status_code=204)
def delete_account(username: str, request: Request,
                   identity: Identity = Depends(require_admin)):
    repos = request.app.state.repos
    # 존재 확인을 가드보다 먼저(set_role 관례): 없는 계정은 409 가 아니라 404.
    account = repos.accounts.get(username)
    if account is None:
        raise HTTPException(status_code=404, detail="account_not_found")
    # 자기 삭제 차단. 토큰 경로 actor 는 node:/shared-token 로 좁혀졌으므로(Task 1)
    # 이 가드는 실질적으로 세션 경로에서만 의미가 있다(설계 §2.3 안전장치 1).
    if identity.actor == username:
        raise HTTPException(status_code=409, detail="cannot_delete_self")
    _guard_last_active_admin(repos, account)
    # 비종단 요청 보유 계정은 삭제하지 않는다 -- 소유자 없는 잡을 예방(설계 §2.3-3).
    if repos.requests.has_active_for_requester(username):
        raise HTTPException(status_code=409, detail="account_has_active_requests")
    repos.accounts.delete(username, actor=audit_actor(identity))
```

- [ ] **Step 4: 통과를 확인한다**

Run:
```
PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_api_admin_accounts.py -q
```
Expected: 전부 PASS.

- [ ] **Step 5: 커밋**

```bash
git add src/dms/api/routes_accounts.py tests/test_api_admin_accounts.py
git commit -m "feat(accounts): DELETE 라우트 + 안전장치 3종(자기 삭제·마지막 관리자·비종단 요청)"
```

---

### Task 5: 마지막 활성 관리자 가드를 set_role 강등·set_disabled 로 확장

현재 마지막 관리자 보호는 어느 경로에도 없다(설계 §1-9). Task 4 의 `_guard_last_active_admin` 을 역할 강등(admin→그 외)과 비활성화(disabled=True)에도 건다.

**Files:**
- Modify: `src/dms/api/routes_accounts.py`
- Test: `tests/test_api_admin_accounts.py`

**Interfaces:**
- Consumes: `_guard_last_active_admin`(Task 4), 기존 `set_role`/`set_disabled` 라우트.
- Produces: set_role 이 admin→비-admin 강등일 때, set_disabled 이 disabled=True 일 때 마지막 활성 관리자면 409 `last_active_admin`. 승격/재활성화는 관리자 수를 줄이지 않으므로 가드 미적용.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_api_admin_accounts.py` 파일 끝에 추가:

```python
def test_demote_last_active_admin_forbidden(client):
    # 유일 admin 을 user 로 강등 시도(토큰 호출, self-guard 미발동) -> 409, 역할 불변.
    _mk_admin(client, "onlyadm2")
    r = client.put("/api/admin/accounts/onlyadm2/role", json={"role": "user"}, headers=ADMIN)
    assert r.status_code == 409 and r.json()["detail"] == "last_active_admin"
    row = next(a for a in client.get("/api/admin/accounts", headers=ADMIN).json()
               if a["username"] == "onlyadm2")
    assert row["role"] == "admin"


def test_disable_last_active_admin_forbidden(client):
    # 유일 admin 을 비활성화 시도 -> 409, disabled 불변.
    _mk_admin(client, "onlyadm3")
    r = client.put("/api/admin/accounts/onlyadm3/disabled", json={"disabled": True},
                   headers=ADMIN)
    assert r.status_code == 409 and r.json()["detail"] == "last_active_admin"
    row = next(a for a in client.get("/api/admin/accounts", headers=ADMIN).json()
               if a["username"] == "onlyadm3")
    assert row["disabled"] == 0


def test_demote_one_of_two_admins_succeeds(client):
    # 대조: admin 이 둘이면 강등 통과.
    _mk_admin(client, "adm_a")
    _mk_admin(client, "adm_b")
    assert client.put("/api/admin/accounts/adm_b/role", json={"role": "user"},
                      headers=ADMIN).status_code == 200


def test_promote_last_admin_is_not_guarded(client):
    # 승격/재활성화는 관리자 수를 줄이지 않으므로 마지막 관리자 가드 대상이 아니다.
    _mk_admin(client, "onlyadm4")
    # admin -> admin(무변화)도 강등이 아니므로 통과해야 한다.
    assert client.put("/api/admin/accounts/onlyadm4/role", json={"role": "admin"},
                      headers=ADMIN).status_code == 200
```

- [ ] **Step 2: 실패를 확인한다**

Run:
```
PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_api_admin_accounts.py -q
```
Expected: FAIL — `test_demote_last_active_admin_forbidden`·`test_disable_last_active_admin_forbidden` 이 `assert 200 == 409`(현재 가드 없어 강등/비활성화가 통과). `test_demote_one_of_two_admins_succeeds`·`test_promote_last_admin_is_not_guarded` 는 이미 PASS(가드가 없어 통과 — 확장 후에도 통과해야 하는 대조).

- [ ] **Step 3: set_role·set_disabled 에 가드를 넣는다**

`src/dms/api/routes_accounts.py` — `set_role`(:28-43)을 교체한다. 강등일 때만 마지막 관리자 가드를 건다:

```python
@router.put("/api/admin/accounts/{username}/role")
def set_role(username: str, body: RoleBody, request: Request,
             identity: Identity = Depends(require_admin)):
    repos = request.app.state.repos
    # 존재 확인을 self-guard보다 먼저: 존재하지 않는 계정을 대상으로 하면
    # (설령 그 이름이 자신의 actor와 같더라도) 409가 아니라 404여야 한다.
    account = repos.accounts.get(username)
    if account is None:
        raise HTTPException(status_code=404, detail="account_not_found")
    _guard_self(identity, username)
    # 강등(admin -> 그 외)만 마지막 관리자 가드 대상. 승격은 관리자 수를 안 줄인다.
    if body.role != ROLE_ADMIN:
        _guard_last_active_admin(repos, account)
    try:
        repos.accounts.set_role(username, body.role, actor=audit_actor(identity))
    except KeyError:
        raise HTTPException(status_code=404, detail="account_not_found")
    except DomainValidationError as e:
        raise HTTPException(status_code=422, detail=e.reason_code)
    return repos.accounts.get(username)
```

`set_disabled`(:46-57)을 교체한다. 비활성화(True)일 때만 가드:

```python
@router.put("/api/admin/accounts/{username}/disabled")
def set_disabled(username: str, body: DisabledBody, request: Request,
                  identity: Identity = Depends(require_admin)):
    repos = request.app.state.repos
    account = repos.accounts.get(username)
    if account is None:
        raise HTTPException(status_code=404, detail="account_not_found")
    _guard_self(identity, username)
    # 비활성화만 마지막 관리자 가드 대상. 재활성화는 관리자 수를 안 줄인다.
    if body.disabled:
        _guard_last_active_admin(repos, account)
    try:
        repos.accounts.set_disabled(username, body.disabled, actor=audit_actor(identity))
    except KeyError:
        raise HTTPException(status_code=404, detail="account_not_found")
    return repos.accounts.get(username)
```

(`_guard_self`(:17-20)는 그대로 — 자기 대상은 여전히 409 `cannot_lock_self` 로 마지막 관리자 가드보다 먼저 걸린다. 기존 `test_self_lock_role_forbidden`·`test_self_lock_disabled_forbidden` 이 이 순서를 고정한다.)

- [ ] **Step 4: 통과를 확인한다**

Run:
```
PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest tests/test_api_admin_accounts.py -q
```
Expected: 전부 PASS. 특히 기존 `test_self_lock_role_forbidden`(:65-79)이 `cannot_lock_self`(409)를 유지 — self-guard 가 마지막 관리자 가드보다 먼저다.

- [ ] **Step 5: 백엔드 전체 회귀를 확인한다**

Run(포그라운드, Bash timeout 600000ms):
```
PYTHONPATH=/home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20/src /home/mason/dms-dev/dms/.venv/bin/python -m pytest -q
```
Expected: 전부 PASS — 기준선 998 + 이번 슬라이스 신규(대략 +23: actor_spoof 5, identity 1, planner 2, migrations 1, repo_accounts_admin 3, api_admin_accounts 삭제 6·마지막관리자 4, api_auth/control 갱신은 순증 0 — 정확 수는 실행이 확정한다).

- [ ] **Step 6: 커밋**

```bash
git add src/dms/api/routes_accounts.py tests/test_api_admin_accounts.py
git commit -m "feat(accounts): 마지막 활성 관리자 가드를 강등·비활성화로 확장(세 경로 전부)"
```

---

### Task 6: 프론트 — 삭제 버튼 + 사용자명 재입력 확인 다이얼로그

`AccountsList.tsx` 각 행에 삭제 버튼을 더한다. 클릭 시 사용자명을 재입력해야 삭제가 활성화되는 확인 다이얼로그를 띄운다. 자기 계정·마지막 활성 관리자는 버튼을 비활성화하고 사유를 다르게 낸다. 프론트 가드는 보안 경계가 아니다 — 서버가 Task 4·5 로 다시 강제한다.

**Files:**
- Modify: `frontend/src/lib/api.ts`, `frontend/src/features/accounts/useAccounts.ts`, `frontend/src/features/accounts/AccountsList.tsx`
- Create: `frontend/src/features/accounts/DeleteAccountDialog.tsx`
- Test: `frontend/src/features/accounts/useAccounts.test.tsx`, `frontend/src/features/accounts/AccountsList.test.tsx`

**Interfaces:**
- Consumes: `apiSend("DELETE", ...)`(`api.ts`), `Dialog`(`components/ui/Dialog`), `Button`, `Account` 타입, `useMe`.
- Produces: `useDeleteAccount()`(DELETE `/api/admin/accounts/:username`, `["accounts"]` 무효화), `DeleteAccountDialog`, 삭제 버튼/비활성 사유, 새 사유 문구 3종.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`frontend/src/features/accounts/useAccounts.test.tsx` 파일 끝에 추가:

```tsx
test("useDeleteAccount DELETEs /api/admin/accounts/:username", async () => {
  let called = false;
  server.use(http.delete("/api/admin/accounts/victim", () => {
    called = true; return new HttpResponse(null, { status: 204 }); }));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const { result } = renderHook(() => useDeleteAccount(), { wrapper: ({ children }) =>
    <QueryClientProvider client={qc}>{children}</QueryClientProvider> });
  result.current.mutate("victim");
  await waitFor(() => expect(called).toBe(true));
});
```

`useAccounts.test.tsx` import 줄(:6)을 확장:

```tsx
import { useAccounts, useSetRole, useSetDisabled, useDeleteAccount } from "./useAccounts";
```

`frontend/src/features/accounts/AccountsList.test.tsx` 파일 끝에 추가:

```tsx
test("자기 계정은 삭제 버튼 대신 비활성 사유를 보여준다", async () => {
  stubMe("alice");
  server.use(http.get("/api/admin/accounts", () => HttpResponse.json(ACCOUNTS)));
  wrap();
  const selfRow = (await screen.findByText("alice")).closest("tr")!;
  expect(within(selfRow).getByText("자기 계정은 삭제할 수 없습니다")).toBeInTheDocument();
  expect(within(selfRow).queryByRole("button", { name: "삭제" })).toBeNull();
});

test("마지막 활성 관리자는 삭제 버튼 대신 비활성 사유를 보여준다", async () => {
  stubMe("root");   // 어느 행도 자기 자신이 아니다
  server.use(http.get("/api/admin/accounts", () => HttpResponse.json(ACCOUNTS)));
  wrap();
  const adminRow = (await screen.findByText("admin", { selector: "td" })).closest("tr")!;
  expect(within(adminRow).getByText("마지막 관리자는 삭제할 수 없습니다")).toBeInTheDocument();
  expect(within(adminRow).queryByRole("button", { name: "삭제" })).toBeNull();
});

test("관리자가 둘이면 삭제 버튼이 뜬다 (대조)", async () => {
  stubMe("root");
  const two = [
    { username: "admin", role: "admin", email: null, disabled: 0, created_at: "2026-08-05T00:00:00Z" },
    { username: "admin2", role: "admin", email: null, disabled: 0, created_at: "2026-08-05T00:00:00Z" },
  ];
  server.use(http.get("/api/admin/accounts", () => HttpResponse.json(two)));
  wrap();
  const adminRow = (await screen.findByText("admin", { selector: "td" })).closest("tr")!;
  expect(within(adminRow).getByRole("button", { name: "삭제" })).toBeInTheDocument();
});

test("사용자명 재입력이 일치해야 삭제가 전송된다", async () => {
  stubMe("root");
  let deleted = false;
  server.use(
    http.get("/api/admin/accounts", () => HttpResponse.json(ACCOUNTS)),
    http.delete("/api/admin/accounts/alice", () => {
      deleted = true; return new HttpResponse(null, { status: 204 }); }));
  wrap();
  const aliceRow = (await screen.findByText("alice")).closest("tr")!;
  await userEvent.click(within(aliceRow).getByRole("button", { name: "삭제" }));
  const dialog = await screen.findByRole("dialog");
  const confirm = within(dialog).getByRole("button", { name: "계정 삭제" });
  const input = within(dialog).getByRole("textbox", { name: "삭제 확인 사용자명 재입력" });
  // 불일치면 확인 버튼이 비활성 -- 눌러도 삭제가 안 나간다.
  await userEvent.type(input, "wrong");
  expect(confirm).toBeDisabled();
  await userEvent.clear(input);
  await userEvent.type(input, "alice");
  expect(confirm).toBeEnabled();
  await userEvent.click(confirm);
  await waitFor(() => expect(deleted).toBe(true));
});
```

- [ ] **Step 2: 실패를 확인한다**

Run:
```
cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20/frontend && npx vitest run src/features/accounts/useAccounts.test.tsx src/features/accounts/AccountsList.test.tsx
```
Expected: FAIL — useAccounts 1건(`useDeleteAccount` import 부재로 모듈 로드 에러/`is not a function`), AccountsList 4건(`삭제` 버튼/사유 텍스트/다이얼로그 미존재로 `findBy*` 타임아웃).

- [ ] **Step 3: 훅·다이얼로그·목록·문구를 구현한다**

**(1)** `frontend/src/features/accounts/useAccounts.ts` 파일 끝에 추가:

```tsx
export const useDeleteAccount = () => {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (username: string) =>
    apiSend("DELETE", `/api/admin/accounts/${username}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["accounts"] }) });
};
```

**(2)** `frontend/src/features/accounts/DeleteAccountDialog.tsx` (신규 파일 전체):

```tsx
import { useEffect, useState } from "react";
import { Dialog } from "../../components/ui/Dialog";
import { Button } from "../../components/ui/Button";
import { ApiError } from "../../lib/api";
import type { Account } from "../../lib/types";
import { useDeleteAccount } from "./useAccounts";

const field = "mt-1 w-full rounded-lg border border-black/10 px-3 py-2";

export function DeleteAccountDialog({ account }: { account: Account }) {
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const del = useDeleteAccount();
  // 다이얼로그를 닫을 때마다 재입력값과 이전 에러를 비운다(DenyDialog 관례).
  useEffect(() => { if (!open) { setTyped(""); del.reset(); } }, [open]);
  return (
    <Dialog open={open} onOpenChange={setOpen} title="계정 삭제"
            trigger={<Button variant="ghost">삭제</Button>}>
      <div className="space-y-3 text-sm">
        <p>이 작업은 되돌릴 수 없습니다. 삭제하려면 <b>{account.username}</b> 를 그대로 입력하세요.</p>
        {/* 재입력 일치 전엔 확인 버튼을 잠근다(오조작 방지). 프론트 가드는 보안 경계가
            아니다 -- 서버가 자기/마지막관리자/비종단요청을 다시 강제한다(설계 §3). */}
        <input aria-label="삭제 확인 사용자명 재입력" className={field}
               value={typed} onChange={(e) => setTyped(e.target.value)} />
        {del.isError && <p className="text-bad">{(del.error as ApiError).message}</p>}
        <div className="flex justify-end gap-2 pt-2">
          <Button variant="ghost" type="button" onClick={() => setOpen(false)}>취소</Button>
          <Button type="button" disabled={typed !== account.username || del.isPending}
                  onClick={() => del.mutate(account.username,
                                            { onSuccess: () => setOpen(false) })}>
            계정 삭제
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
```

**(3)** `frontend/src/features/accounts/AccountsList.tsx` 를 교체한다(삭제 열 로직 추가):

```tsx
import { useAccounts, useSetRole, useSetDisabled } from "./useAccounts";
import { useMe } from "../auth/useAuth";
import { Table } from "../../components/ui/Table";
import { Button } from "../../components/ui/Button";
import { DeleteAccountDialog } from "./DeleteAccountDialog";
import { ApiError } from "../../lib/api";
import type { Account } from "../../lib/types";

export function AccountsList() {
  const q = useAccounts();
  const me = useMe();
  const setRole = useSetRole();
  const setDisabled = useSetDisabled();

  const mutationError = setRole.isError
    ? (setRole.error as ApiError).message
    : setDisabled.isError
    ? (setDisabled.error as ApiError).message
    : null;

  const toggle = (a: Account) => setDisabled.mutate({ username: a.username, disabled: a.disabled !== 1 });

  const rows = q.data ?? [];
  // 활성 관리자(role=admin AND disabled=0). 하나뿐이면 그 행의 삭제를 막는다 --
  // 서버가 last_active_admin(409)으로 다시 강제하지만, 화면에서 미리 사유를 낸다.
  const activeAdmins = rows.filter((a) => a.role === "admin" && a.disabled === 0);

  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">계정</h1>
      </div>
      {q.isLoading ? <p className="text-muted">불러오는 중…</p> : q.isError ? (
        <p className="text-bad">{(q.error as ApiError).message}</p>
      ) : (
        <Table>
          <thead>
            <tr className="text-muted">
              <th className="py-2">사용자명</th><th>역할</th><th>이메일</th><th>상태</th><th>등록일</th><th>작업</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((a) => {
              const isSelf = me.data?.actor === a.username;
              const isLastActiveAdmin = a.role === "admin" && a.disabled === 0
                && activeAdmins.length === 1;
              return (
                <tr key={a.username} className="border-t border-black/5">
                  <td className="py-2">{a.username}</td>
                  <td>
                    <select
                      aria-label={`${a.username} 역할`}
                      value={a.role}
                      disabled={isSelf}
                      onChange={(e) => setRole.mutate({ username: a.username, role: e.target.value })}
                    >
                      <option value="user">user</option>
                      <option value="admin">admin</option>
                    </select>
                  </td>
                  <td className="text-muted">{a.email ?? "—"}</td>
                  <td>{a.disabled === 1 ? "비활성" : "활성"}</td>
                  <td className="text-muted">{a.created_at}</td>
                  <td className="flex items-center gap-2 py-2">
                    <Button variant="ghost" disabled={isSelf} onClick={() => toggle(a)}>
                      {a.disabled === 1 ? "활성화" : "비활성화"}
                    </Button>
                    {isSelf ? (
                      <span className="text-muted text-xs">자기 계정은 삭제할 수 없습니다</span>
                    ) : isLastActiveAdmin ? (
                      <span className="text-muted text-xs">마지막 관리자는 삭제할 수 없습니다</span>
                    ) : (
                      <DeleteAccountDialog account={a} />
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </Table>
      )}
      {mutationError && <p className="text-bad text-sm mt-2">{mutationError}</p>}
    </section>
  );
}
```

**(4)** `frontend/src/lib/api.ts` — `cannot_lock_self`(:103) 아래에 새 사유 문구 3종을 더한다:

```tsx
  cannot_delete_self: "자기 계정은 삭제할 수 없습니다",
  last_active_admin: "마지막 관리자는 삭제할 수 없습니다",
  account_has_active_requests: "진행 중인 요청이 있어 삭제할 수 없습니다",
```

- [ ] **Step 4: 통과를 확인한다**

Run:
```
cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20/frontend && npx vitest run src/features/accounts/useAccounts.test.tsx src/features/accounts/AccountsList.test.tsx
```
Expected: 전부 PASS. 기존 AccountsList 테스트(`the current user's own row has disabled controls` 등)도 유지 — 삭제 열은 토글 버튼 옆에 추가될 뿐 기존 컨트롤을 바꾸지 않는다(마지막 관리자는 토글이 아니라 삭제만 잠근다 → `toggling status sends the correct PUT body` 가 sole admin 에서도 계속 통과).

- [ ] **Step 5: 프론트 전체 회귀와 타입체크를 확인한다**

Run:
```
cd /home/mason/dms-dev/dms/.claude/worktrees/dms-slice18-20/frontend && npx vitest run && npx tsc -b
```
Expected: 전체 PASS(기준선 215 + 신규: useAccounts 1 + AccountsList 4 = 220), 타입 에러 0.

- [ ] **Step 6: 커밋**

```bash
git add frontend/src/lib/api.ts frontend/src/features/accounts/useAccounts.ts \
  frontend/src/features/accounts/useAccounts.test.tsx \
  frontend/src/features/accounts/DeleteAccountDialog.tsx \
  frontend/src/features/accounts/AccountsList.tsx \
  frontend/src/features/accounts/AccountsList.test.tsx
git commit -m "feat(portal): 계정 삭제 버튼 + 사용자명 재입력 확인 다이얼로그(자기/마지막 관리자 비활성 사유)"
```

---

## 플랜 이후: 배포·실증 (설계 §6 — 별도 ops, 플랜 밖)

플랜 실행(6태스크 커밋)이 끝나면 컨트롤러가 테스트베드에서 수행한다 — 플랜 태스크가 아니다(슬라이스 12~17 과 동일 관례). 에이전트 코드는 안 바뀌었으므로 `dms` 이미지만 범프한다. **순서가 실증의 일부다** — 고치기 전 RED 를 먼저 봐야 §6-1 이 의미를 가진다:

1. (§6-1 고치기 전 RED) 배포 전, 이미지 범프 없이 현재 라이브에서 공유 토큰 + `x-dms-actor: root` 로 sync 요청을 넣어 preflight 파드 `runAsUser=0` 이 실제로 나오는 것을 확인(스푸핑 재현).
2. `deploy/k8s` 태그를 새 값으로 범프 후 빌드/푸시, `kubectl apply`(migrate initContainer 가 `requests.auth_method` 컬럼을 기배포 DB 에 보강).
3. (§6-2 고친 뒤) 같은 요청이 **400 `invalid_actor`** 로 막히는지.
4. (§6-3 에이전트 무손상) `node:<이름>` 경로로 에이전트 리포트가 계속 들어오는지 — 이게 깨지면 노드 리포트가 전부 멈춘다(가장 먼저 확인).
5. (§6-4) 슬라이스 3 실증이 만든 임시 관리자 **`s3verify`**(`BACKLOG.md:91`)를 세션 로그인한 다른 관리자로 실제 삭제하고, `user_scan_paths` 데드 로우가 함께 사라지는지.
6. (§6-5 마지막 관리자 보호) 관리자를 하나만 남긴 뒤 삭제·강등·비활성화 시도가 409 로 거부되는지.
7. (§6-6 비종단 요청 가드) 비종단 요청을 가진 계정 삭제가 409 로 거부되는지.

**잡을 제출하는** 수기 실증은 세션 로그인(쿠키 curl)으로 한다 — 토큰 경로는 이제 actor 가 shared-token/node:<이름> 뿐이라 잡 제출이 사실상 막힌다(설계 §2.2-3). 운영·조회용 admin curl 은 `Bearer` 토큰만으로 그대로 동작한다.

## Self-Review

**1. 스펙 커버리지**

| 설계 절 | 담당 태스크 |
|---|---|
| §1 실측 전제(actor 신뢰, 특권 기본 열림, uid 파드 주입, admission 무력, 신원 plan 시점 구움, FK 0건, 삭제 부재, 마지막 관리자 무보호, 세션 쿠키뿐, shared≠admin 토큰) | 실측 고정값 표 + 각 태스크 근거 주석 |
| §2.1 스푸핑 실재·기본 열림 | Task 1(재현 RED) |
| §2.2-1 토큰 actor 를 node:<이름> 으로 좁힘(그 외 400, 빈값→shared-token) | Task 1(auth 게이트 + _NODE_NAME_RE 단일 출처) |
| §2.2-2 특권 승격 session 전용 | Task 2(resolve_job_identity session_authenticated + requests.auth_method + planner/routes/batch 배선) |
| §2.2-4 기존 21파일 정리 + 감사 단언 갱신(지우지 않음) | Task 1 Step 6·7 |
| §2.3 하드 삭제(accounts+user_scan_paths+감사 한 트랜잭션) | Task 3 |
| §2.3 안전장치 1 자기 삭제 | Task 4 |
| §2.3 안전장치 2 마지막 활성 관리자(삭제·강등·비활성화 세 경로) | Task 4(삭제) + Task 5(강등·비활성화) |
| §2.3 안전장치 3 비종단 요청 보유 409 | Task 2(has_active_for_requester) + Task 4(라우트) |
| §3 화면(삭제 버튼·재입력 확인·자기/마지막 관리자 사유·409 표면화) | Task 6 |
| §4 오류 처리(400 invalid_actor, 404 account_not_found, 409 세 종) | Task 1·4·5 |
| §5 테스트(actor 계약·기존 정리·삭제·안전장치·프론트) | Task 1·3·4·5·6 각 Step 1 |
| §6 실증 | 플랜 이후 절(관례 — 플랜 태스크 아님) |
| §7 하지 않는 것(메일 인증, 토큰 회전, 서버측 세션, 병합/개명, 이력 익명화) | 어떤 태스크도 건드리지 않음 — 삭제는 actor 문자열을 이력으로 남긴다(§2.3 결정) |

**2. 플레이스홀더 점검** — "TBD"/"적절히 처리"/코드 없는 테스트 지시 없음. 모든 코드 단계(신규 파일 2 + 프론트 신규 1 포함)에 전문이 있고, 21파일 정리는 파일:줄과 정확한 치환을 나열했다. 다른 태스크 참조는 Interfaces 블록의 시그니처로만 한다.

**3. RED 가 무엇을 붙잡는지(실패 메시지 명시)** — Task 1 스푸핑 RED 는 `assert 202 == 400`(서버가 `x-dms-actor: root` 를 받아 requester_id=root 요청을 만든다 = 구멍이 열려 있다)와 `AttributeError: module 'dms.api.auth' has no attribute '_NODE_NAME_RE'`. Task 2 는 `TypeError: unexpected keyword 'session_authenticated'`/`'auth_method'` 와 컬럼 부재 `AssertionError`. Task 3 는 `AttributeError: no attribute 'delete'`. Task 4 는 `405 Method Not Allowed`(라우트 부재). Task 5 는 `assert 200 == 409`(가드 없어 강등/비활성화가 통과). Task 6 은 import/`findBy*` 타임아웃. 각 실패는 실제로 깨뜨려 확인하는 스텝(Step 2)에 고정돼 있다.

**4. 타입·철자 일관성** — `auth_method` 는 컬럼(migrations)·`create` 파라미터·planner 조회·routes/batch 전달·테스트 시드가 동일 철자. `session_authenticated` 는 identity 파라미터·planner 전달·테스트가 동일. actor 게이트 산출 `invalid_actor`(400)·`node:<이름>`·`shared-token` 은 auth·테스트·프론트 문구가 일관. 안전장치 detail `cannot_delete_self`·`last_active_admin`·`account_has_active_requests` 는 라우트·테스트·`api.ts` REASON_MESSAGES 가 같은 철자. `_NODE_NAME_RE` 는 auth 정의·routes_agent import·회귀 테스트가 같은 객체(`is` 단언)를 본다.

**알려진 위험:**
- **`auth_method` NULL 폴백**: 기배포 DB 의 기존 요청 행은 ALTER 로 추가된 컬럼이 NULL 이라 `req.get("auth_method") == "session"` 이 False → 비특권으로 강등된다. 특권으로 실행 중이던 in-flight 요청이 있으면 신원이 바뀔 수 있으나, 방향이 fail-closed(특권 제거)라 안전하고 그런 in-flight 특권 요청은 드물다. 신규 요청은 제출/materialize 가 항상 값을 채운다.
- **배치 자식은 항상 비특권**: `_materialize` 가 `auth_method="token"` 을 박으므로 세션 admin 이 만든 배치라도 자식은 특권을 못 얻는다. 배치를 root 로 돌리는 흐름은 지원 대상이 아니며(설계 밖) 안전 기본이다.
- **프론트 마지막 관리자 판정은 힌트**: `activeAdmins.length === 1` 은 클라이언트 계산이라 목록이 낡으면 어긋날 수 있다 — 서버가 `last_active_admin`(409)으로 최종 강제하고, 다이얼로그가 그 문구를 그대로 표면화한다.
- **토글 vs 삭제 비대칭**: 마지막 활성 관리자는 삭제 버튼만 잠그고 비활성화 토글은 잠그지 않는다(기존 `toggling status` 테스트 유지 + 서버 409 강제). 의도된 선택이며 서버가 실 경계다.
- **`test_nonexistent_target_is_404_even_when_name_equals_self_actor`**: Task 1 정리 후 actor 가 `shared-token` 이라 target `ops` 와 더는 일치하지 않지만, 존재 확인이 먼저라 404 단언은 그대로 성립한다(테스트 통과, 주석의 "ops" 가정은 약화되나 회귀 아님).
