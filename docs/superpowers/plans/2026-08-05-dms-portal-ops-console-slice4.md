# DMS 포탈 슬라이스 4 (운영 제어 콘솔) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 운영자가 포탈에서 도구별 정책을 편집하고, identity denylist를 등재/해제하며, 유지보수·드레인을 켜고 끌 수 있다. 신규 설치는 기본 정책이 시드되어 즉시 동작하고, denylist는 특권 경로보다 먼저 평가된다.

**Architecture:** 백엔드는 4건만 신규다 — (1) `migrate()`의 정책 기본 시드, (2) `routes_control.py`의 control-state GET/PUT, (3) `maintenance` 소비처(제출 503), (4) `identity.py`의 denylist group 평가 순서 교정. 정책·denylist CRUD API는 이미 완성돼 있어 프론트만 붙인다. 프론트는 슬라이스 1·3의 C/Soft-SaaS 컴포넌트(`Table`/`Dialog`/`Button`)·`apiGet`/`apiSend`·TanStack Query 패턴을 그대로 재사용한다.

**Tech Stack:** Python 3.11 / FastAPI / SQLite·PostgreSQL 듀얼 다이얼렉트 / pytest · React 18 + Vite 5 + TS + Tailwind + Radix + TanStack Query v5 + Vitest · Testing Library · MSW 2

## Global Constraints

- 설계 문서 `docs/superpowers/specs/2026-08-05-dms-portal-ops-console-slice4-design.md`가 상위 규칙이다. 충돌 시 `2026-08-02-dms-clean-slate-design.md`(상위 스펙)가 이긴다.
- **정책 CRUD(`routes_policies.py`)·denylist CRUD(`routes_denylist.py`)·`ControlRepository`의 기존 메서드는 수정하지 않는다.** 신규는 설계 §0.1의 4건뿐.
- 정책 시드는 **멱등**하고 **기존 행을 절대 덮어쓰지 않는다**. `updated_by = "migration-seed"`.
- 시드 기본값(스펙 §5, 초 단위): scan `execution=3600, preview=NULL` / dsync·nsync `preview=3600, execution=259200` / rm `preview=1800, execution=3600`. 공통 `queue="dms-data"`, `default_priority="mid"`, `max_priority="high"`, `enabled=1`, `procs_per_node=8`. `max_nodes`: scan 4, dsync 8, nsync 8, rm 4.
- 모든 admin 화면은 `RequireRole role="admin"`으로 감싸고, 백엔드는 `require_admin`을 유지한다.
- 프론트 mutation은 성공 시 해당 쿼리 키를 무효화한다: 정책 `["policies"]`, denylist `["denylist"]`, 컨트롤 상태 `["control-state"]`.
- 다이얼로그는 닫을 때 mutation 에러를 `reset()`한다(슬라이스 3에서 확립한 관례).
- 한국어 UI 문자열을 쓴다. 이모지 금지, 왼쪽 점 상태 뱃지 금지.
- 커밋은 태스크 단위로 하고, 각 태스크는 테스트가 GREEN인 상태로 끝난다.

---

### Task 1: 정책 기본 시드 (migrations)

**Files:**
- Modify: `src/dms/migrations.py` (control_state 시드 직후, `migrate()` 안)
- Modify: `tests/test_api_policies.py` (시드로 깨지는 기존 단언 갱신)
- Modify: `tests/test_planner.py` (`test_missing_policy_rejects` — 시드된 행을 지우고 나서 검증)
- Test: `tests/test_migrations_policy_seed.py` (신규)

**Interfaces:**
- Consumes: `migrate(db)` (`src/dms/migrations.py`), `POLICY_TOOLS = ("scan","dsync","nsync","rm")` (`src/dms/repositories/control.py:4`)
- Produces: `migrate()` 실행 후 `policies` 테이블에 4행 존재. 이후 태스크·실증이 이를 전제한다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_migrations_policy_seed.py`:

```python
from dms.migrations import migrate

EXPECTED = {
    "scan":  {"max_nodes": 4, "preview_timeout_seconds": None, "execution_timeout_seconds": 3600},
    "dsync": {"max_nodes": 8, "preview_timeout_seconds": 3600, "execution_timeout_seconds": 259200},
    "nsync": {"max_nodes": 8, "preview_timeout_seconds": 3600, "execution_timeout_seconds": 259200},
    "rm":    {"max_nodes": 4, "preview_timeout_seconds": 1800, "execution_timeout_seconds": 3600},
}


def test_seeds_four_default_policies(db):
    rows = {r["tool"]: r for r in db.query("SELECT * FROM policies")}
    assert set(rows) == set(EXPECTED)
    for tool, want in EXPECTED.items():
        row = rows[tool]
        for key, value in want.items():
            assert row[key] == value, f"{tool}.{key}"
        assert row["procs_per_node"] == 8
        assert row["queue"] == "dms-data"
        assert row["default_priority"] == "mid"
        assert row["max_priority"] == "high"
        assert row["enabled"] == 1
        assert row["updated_by"] == "migration-seed"


def test_seed_is_idempotent_and_never_overwrites(db):
    db.execute("UPDATE policies SET max_nodes = 99, updated_by = 'ops' WHERE tool = 'scan'")
    migrate(db)
    row = db.query_one("SELECT * FROM policies WHERE tool = 'scan'")
    assert row["max_nodes"] == 99
    assert row["updated_by"] == "ops"
    assert len(db.query("SELECT * FROM policies")) == 4
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_migrations_policy_seed.py -v`
Expected: FAIL — `set(rows) == set(EXPECTED)`에서 빈 집합이라 AssertionError

- [ ] **Step 3: 시드를 구현한다**

`src/dms/migrations.py`의 control_state 싱글톤 시드(`INSERT INTO control_state ...`) **바로 다음**에 추가한다:

```python
    # 도구별 기본 정책 시드 (스펙 §5 "phase별 타임아웃은 정책 행에서"). 멱등하며
    # 기존 행은 절대 덮어쓰지 않는다 — 운영자가 포탈에서 고친 값을 마이그레이션이
    # 되돌리면 안 된다. 행이 없으면 planner가 missing_policy로 전부 거부한다.
    now = utc_now_iso()
    for tool, max_nodes, preview_timeout, execution_timeout in (
        ("scan", 4, None, 3600),
        ("dsync", 8, 3600, 259200),
        ("nsync", 8, 3600, 259200),
        ("rm", 4, 1800, 3600),
    ):
        db.execute(
            """INSERT INTO policies (tool, max_nodes, procs_per_node, queue,
                   default_priority, max_priority, preview_timeout_seconds,
                   execution_timeout_seconds, enabled, updated_at, updated_by)
               SELECT :t, :mn, 8, 'dms-data', 'mid', 'high', :pt, :et, 1, :now,
                      'migration-seed'
               WHERE NOT EXISTS (SELECT 1 FROM policies WHERE tool = :t)""",
            {"t": tool, "mn": max_nodes, "pt": preview_timeout,
             "et": execution_timeout, "now": now})
```

`utc_now_iso`가 이미 `migrations.py`에 import돼 있는지 확인하고, 없으면 기존 import 줄에 추가한다(`from .db import ... utc_now_iso`). 파일 상단의 기존 import 스타일을 따른다.

- [ ] **Step 4: 통과를 확인한다**

Run: `python -m pytest tests/test_migrations_policy_seed.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: 시드 때문에 깨지는 기존 테스트를 갱신한다**

시드는 의도된 동작 변경이므로, 두 테스트의 **전제**를 명시적으로 바꾼다.

`tests/test_api_policies.py`의 `test_policy_crud` — 시드로 4행이 존재하므로 "dsync만 존재"·"scan 404" 단언이 더 이상 참이 아니다. 다음으로 교체한다:

```python
def test_policy_crud(client):
    assert client.put("/api/admin/policies/dsync", json=BODY,
                      headers=ADMIN).status_code == 200
    assert client.get("/api/admin/policies/dsync",
                      headers=ADMIN).json()["max_nodes"] == 3
    listed = client.get("/api/admin/policies", headers=ADMIN).json()
    # migrate()가 4개 도구를 시드하므로 목록은 항상 4행이고 정렬돼 있다
    assert [p["tool"] for p in listed] == ["dsync", "nsync", "rm", "scan"]
    # 시드된 scan 정책은 조회된다 (시드 전에는 404였다)
    assert client.get("/api/admin/policies/scan", headers=ADMIN).status_code == 200
    assert client.put("/api/admin/policies/dcp", json=BODY,
                      headers=ADMIN).status_code == 422
    assert client.put("/api/admin/policies/scan",
                      json={**BODY, "max_nodes": 0},
                      headers=ADMIN).status_code == 422
```

정렬 순서는 `routes_policies.list_policies`가 `sorted(POLICY_TOOLS)`를 쓰므로 `["dsync","nsync","rm","scan"]`이다.

`tests/test_planner.py`의 `test_missing_policy_rejects` — 시드된 scan 행을 지워 "정책 없음"을 명시적으로 만든다:

```python
def test_missing_policy_rejects(db):
    repos = Repositories(db)
    _seed_storage(repos); _seed_report(repos)
    db.execute("DELETE FROM policies WHERE tool = 'scan'")  # 시드된 기본 정책 제거
    rid = _scan_request(repos)
    assert _planner(repos).run_once(now_iso=NOW)[rid] == "rejected:missing_policy"
```

- [ ] **Step 6: 전체 백엔드 테스트가 GREEN인지 확인한다**

Run: `python -m pytest tests/ -q`
Expected: 전부 통과. 실패가 남으면 그 테스트도 시드 전제 때문인지 확인하고 같은 방식으로 전제를 명시화한다(정책을 지우거나 기대값을 시드에 맞춘다). **테스트를 삭제하거나 단언을 약화시키지 말 것.**

- [ ] **Step 7: 커밋**

```bash
git add src/dms/migrations.py tests/test_migrations_policy_seed.py tests/test_api_policies.py tests/test_planner.py
git commit -m "feat(migrations): seed default per-tool policies (idempotent, never overwrites)"
```

---

### Task 2: 컨트롤 상태 API

**Files:**
- Create: `src/dms/api/routes_control.py`
- Modify: `src/dms/api/app.py` (라우터 등록)
- Test: `tests/test_api_control.py` (신규)

**Interfaces:**
- Consumes: `ControlRepository.control_state()`, `ControlRepository.set_control_state(maintenance=, drain=, reason=, actor=)` (`src/dms/repositories/control.py:88-103`), `require_admin`/`Identity` (`src/dms/api/auth.py`)
- Produces: `GET /api/admin/control-state`, `PUT /api/admin/control-state`. Task 3(maintenance 소비)과 Task 8(프론트 화면)이 이 계약을 쓴다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_api_control.py`:

```python
ADMIN = {"Authorization": "Bearer tok-shared", "x-dms-actor": "ops"}


def test_control_state_requires_admin(client):
    assert client.get("/api/admin/control-state").status_code == 401
    client.post("/api/auth/signup", json={"username": "u1", "password": "p"})
    client.post("/api/auth/login", json={"username": "u1", "password": "p"})
    assert client.get("/api/admin/control-state").status_code == 403


def test_control_state_get_defaults(client):
    body = client.get("/api/admin/control-state", headers=ADMIN).json()
    assert body["maintenance"] == 0
    assert body["drain"] == 0


def test_control_state_put_updates_and_returns_current(client):
    res = client.put("/api/admin/control-state",
                     json={"maintenance": True, "drain": False, "reason": "점검"},
                     headers=ADMIN)
    assert res.status_code == 200
    body = res.json()
    assert body["maintenance"] == 1 and body["drain"] == 0
    assert body["reason"] == "점검"
    assert client.get("/api/admin/control-state", headers=ADMIN).json()["maintenance"] == 1


def test_control_state_put_is_audited(client, db):
    client.put("/api/admin/control-state",
               json={"maintenance": False, "drain": True, "reason": None},
               headers=ADMIN)
    rows = db.query("SELECT * FROM audit_log WHERE mutation_class = 'control_state'")
    assert len(rows) == 1
    assert rows[0]["operation"] == "set"
```

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_api_control.py -v`
Expected: FAIL — 404 (라우트 없음)

- [ ] **Step 3: 라우터를 구현한다**

`src/dms/api/routes_control.py` (신규). `routes_policies.py`의 구조를 그대로 따른다:

```python
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from .auth import Identity, require_admin

router = APIRouter(dependencies=[Depends(require_admin)])


class ControlStateBody(BaseModel):
    maintenance: bool
    drain: bool
    reason: str | None = None


@router.get("/api/admin/control-state")
def get_control_state(request: Request):
    return request.app.state.repos.control.control_state()


@router.put("/api/admin/control-state")
def put_control_state(body: ControlStateBody, request: Request,
                      identity: Identity = Depends(require_admin)):
    control = request.app.state.repos.control
    control.set_control_state(maintenance=body.maintenance, drain=body.drain,
                              reason=body.reason, actor=identity.actor)
    return control.control_state()
```

- [ ] **Step 4: 라우터를 등록한다**

`src/dms/api/app.py`에서 기존 라우터 등록 줄들(예: `routes_policies`, `routes_denylist`)을 찾아 같은 형태로 `routes_control`을 추가한다. 기존 import·등록 스타일을 그대로 따를 것.

- [ ] **Step 5: 통과를 확인한다**

Run: `python -m pytest tests/test_api_control.py -v`
Expected: PASS (4 passed)

- [ ] **Step 6: 커밋**

```bash
git add src/dms/api/routes_control.py src/dms/api/app.py tests/test_api_control.py
git commit -m "feat(api): control-state get/put endpoints"
```

---

### Task 3: maintenance 소비처 — 신규 제출 차단

**Files:**
- Modify: `src/dms/api/routes_requests.py` (`submit`)
- Modify: `src/dms/api/routes_batches.py` (배치 생성 핸들러)
- Test: `tests/test_api_maintenance.py` (신규)

**Interfaces:**
- Consumes: `request.app.state.repos.control.control_state()` → dict with `maintenance`/`drain` (0/1)
- Produces: `503 maintenance_mode`. Task 5의 reason_code 맵과 Task 8의 경고 배너가 이 코드를 쓴다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_api_maintenance.py`. 배치 생성 요청 바디는 `tests/test_api_batches.py`의 기존 생성 테스트를 열어 그대로 복사해 쓴다(필드명을 임의로 지어내지 말 것).

```python
ADMIN = {"Authorization": "Bearer tok-shared", "x-dms-actor": "ops"}
SCAN = {"operation": "scan", "storage": "s1", "target": "data", "priority": "mid"}


def _set(client, *, maintenance, drain=False):
    return client.put("/api/admin/control-state",
                      json={"maintenance": maintenance, "drain": drain, "reason": None},
                      headers=ADMIN)


def test_submit_blocked_during_maintenance(client):
    assert client.post("/api/user/requests", json=SCAN, headers=ADMIN).status_code == 202
    _set(client, maintenance=True)
    res = client.post("/api/user/requests", json=SCAN, headers=ADMIN)
    assert res.status_code == 503
    assert res.json()["detail"] == "maintenance_mode"


def test_submit_allowed_after_maintenance_off(client):
    _set(client, maintenance=True)
    assert client.post("/api/user/requests", json=SCAN, headers=ADMIN).status_code == 503
    _set(client, maintenance=False)
    assert client.post("/api/user/requests", json=SCAN, headers=ADMIN).status_code == 202


def test_drain_does_not_block_submission(client):
    _set(client, maintenance=False, drain=True)
    assert client.post("/api/user/requests", json=SCAN, headers=ADMIN).status_code == 202
```

여기에 배치 생성이 유지보수 중 503을 받는 테스트를 하나 더 추가한다(`test_batch_create_blocked_during_maintenance`), 바디는 위에서 확인한 기존 배치 테스트의 것을 사용한다.

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_api_maintenance.py -v`
Expected: FAIL — 유지보수 중에도 202가 반환됨

- [ ] **Step 3: 공용 가드를 만들고 두 제출 경로에 건다**

`src/dms/api/routes_requests.py`에 헬퍼를 두고 `submit` 맨 앞에서 호출한다(특권 게이트보다 먼저):

```python
def _reject_when_maintenance(request: Request) -> None:
    # 유지보수 창에는 신규 유입을 막는다 (진행 중인 잡은 건드리지 않는다 — 그건 drain의 몫).
    # 관리자도 예외가 아니다. 콘솔의 control-state PUT은 제출 경로가 아니라 잠기지 않는다.
    state = request.app.state.repos.control.control_state()
    if state and state["maintenance"]:
        raise HTTPException(status_code=503, detail="maintenance_mode")
```

`routes_batches.py`의 배치 생성 핸들러 맨 앞에서도 같은 검사를 한다 — `from .routes_requests import _reject_when_maintenance`로 재사용하되, 순환 import가 생기면 두 파일이 공유하는 위치(예: `src/dms/api/auth.py`가 아니라 새 헬퍼 모듈이나 각 파일의 로컬 함수)로 옮긴다. 중복 정의보다 재사용을 우선하되, import 사이클이 생기면 `src/dms/api/guards.py`를 새로 만들어 거기에 둔다.

- [ ] **Step 4: 통과를 확인한다**

Run: `python -m pytest tests/test_api_maintenance.py -v`
Expected: PASS

- [ ] **Step 5: 회귀가 없는지 확인한다**

Run: `python -m pytest tests/ -q`
Expected: 전부 통과 (기본 maintenance=0이므로 기존 제출 테스트는 영향 없음)

- [ ] **Step 6: 커밋**

```bash
git add src/dms/api/ tests/test_api_maintenance.py
git commit -m "feat(api): block new submissions while maintenance is on"
```

---

### Task 4: denylist group 평가를 특권 경로보다 먼저

**Files:**
- Modify: `src/dms/repositories/control.py` (`has_group_denies` 추가)
- Modify: `src/dms/identity.py` (`resolve_job_identity`)
- Test: `tests/test_identity_denylist_order.py` (신규)

**Interfaces:**
- Consumes: `control.is_denied(requester=, owner=, groups=)`, `resolver.resolve(owner)` → `.groups`
- Produces: `ControlRepository.has_group_denies() -> bool`

**배경:** 스펙 §5는 "denylist(requester/owner/group, 대소문자 무관)가 최우선 kill-switch. privileged 경로보다 먼저 평가된다"고 규정한다. 현재 `src/dms/identity.py`의 1차 검사는 `groups=[]`를 넘기므로 **group으로만 등재된 대상이 특권 단축 경로로 통과**한다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_identity_denylist_order.py`. 기존 identity 테스트(`tests/`에서 `resolve_job_identity`를 쓰는 파일)를 먼저 열어 스텁 resolver·repos 생성 방식을 그대로 따른다.

```python
import pytest
from dms.identity import IdentityRejected, resolve_job_identity, StubIdentityResolver
from dms.repositories import Repositories


def test_group_denylist_blocks_privileged_requester(db):
    repos = Repositories(db)
    repos.control.deny("group", "wheel", "정책 위반", "admin")
    resolver = StubIdentityResolver({"root-ish": (1000, 1000, ["wheel"])})
    with pytest.raises(IdentityRejected) as e:
        resolve_job_identity(repos.control, resolver,
                             requester_id="admin", owner_username="root-ish",
                             allow_privileged=True, privileged_requesters=("admin",))
    assert e.value.reason_code == "identity_denied"


def test_privileged_path_needs_no_resolver_when_no_group_denies(db):
    repos = Repositories(db)
    repos.control.deny("requester", "mallory", None, "admin")
    identity = resolve_job_identity(repos.control, None,
                                    requester_id="admin", owner_username="alice",
                                    allow_privileged=True, privileged_requesters=("admin",))
    assert identity.privileged is True
    assert identity.uid == 0


def test_has_group_denies(db):
    repos = Repositories(db)
    assert repos.control.has_group_denies() is False
    repos.control.deny("requester", "bob", None, "admin")
    assert repos.control.has_group_denies() is False
    repos.control.deny("group", "wheel", None, "admin")
    assert repos.control.has_group_denies() is True
```

`StubIdentityResolver`의 실제 생성자 시그니처와 `ResolvedIdentity`의 필드명(`privileged`/`uid`)은 `src/dms/identity.py`를 열어 확인하고 테스트를 그에 맞춘다. 두 번째 테스트의 `resolver=None`은 group 규칙이 없을 때 특권 경로가 LDAP 없이 통과함을 증명하는 것이 목적이다.

- [ ] **Step 2: 실패를 확인한다**

Run: `python -m pytest tests/test_identity_denylist_order.py -v`
Expected: 1번째 테스트 FAIL(특권 경로로 통과해 예외가 안 남), 3번째 FAIL(`has_group_denies` 없음)

- [ ] **Step 3: `has_group_denies`를 추가한다**

`src/dms/repositories/control.py`의 `list_denylist` 옆에:

```python
    def has_group_denies(self) -> bool:
        row = self._db.query_one(
            "SELECT COUNT(*) AS n FROM identity_denylist WHERE subject_type = 'group'")
        return bool(row and row["n"])
```

- [ ] **Step 4: 평가 순서를 교정한다**

`src/dms/identity.py`의 `resolve_job_identity`에서 1차 검사와 특권 단축 사이를 다음으로 바꾼다:

```python
    owner = (owner_username or requester_id).strip()
    # denylist는 최우선 kill-switch이고 특권 경로보다 먼저 평가된다 (스펙 §5).
    # group 규칙이 등재돼 있을 때만 특권 경로에서도 그룹을 해석한다 — 규칙이 없으면
    # 특권 경로는 지금처럼 LDAP 없이 통과한다.
    groups: list[str] = []
    privileged = allow_privileged and requester_id in privileged_requesters
    if privileged and control.has_group_denies():
        if resolver is None:
            raise IdentityRejected("ldap_not_configured")
        try:
            probe = resolver.resolve(owner)
        except IdentityUnavailable as exc:
            raise IdentityRejected("ldap_unavailable", str(exc)[:200])
        if probe is not None:
            groups = list(probe.groups)
    denied = control.is_denied(requester=requester_id, owner=owner, groups=groups)
    if denied:
        raise IdentityRejected("identity_denied", denied)
    if privileged:
        return ResolvedIdentity(owner, 0, 0, (), True)
```

이후의 비특권 경로(LDAP 해석 → 2차 `is_denied` → `register_probe_target` → 반환)는 **그대로 둔다**.

- [ ] **Step 5: 통과를 확인한다**

Run: `python -m pytest tests/test_identity_denylist_order.py -v`
Expected: PASS (3 passed)

- [ ] **Step 6: 회귀가 없는지 확인한다**

Run: `python -m pytest tests/ -q`
Expected: 전부 통과. 특권 경로 관련 기존 테스트(`tests/test_api_requests_privileged.py` 등)가 깨지면 group 규칙이 없는 경로가 바뀌지 않았는지 다시 확인한다.

- [ ] **Step 7: 커밋**

```bash
git add src/dms/identity.py src/dms/repositories/control.py tests/test_identity_denylist_order.py
git commit -m "fix(identity): evaluate group denylist before the privileged shortcut"
```

---

### Task 5: 프론트 타입 · reason 코드 · 훅 3종

**Files:**
- Modify: `frontend/src/lib/types.ts`
- Modify: `frontend/src/lib/api.ts` (REASON_MESSAGES)
- Create: `frontend/src/features/policies/usePolicies.ts`
- Create: `frontend/src/features/denylist/useDenylist.ts`
- Create: `frontend/src/features/control/useControlState.ts`
- Test: `frontend/src/features/policies/usePolicies.test.tsx` (신규)

**Interfaces:**
- Consumes: `apiGet`/`apiSend` (`frontend/src/lib/api.ts`), 백엔드 계약 — `GET /api/admin/policies`, `PUT /api/admin/policies/{tool}`, `GET /api/admin/identity-denylist`, `PUT|DELETE /api/admin/identity-denylist/{subject_type}/{subject}`, `GET|PUT /api/admin/control-state`
- Produces: 타입 `Policy`·`DenyEntry`·`ControlState`, 훅 `usePolicies`·`useUpsertPolicy`·`useDenylist`·`useDeny`·`useAllow`·`useControlState`·`useSetControlState`. Task 6·7·8이 쓴다.

- [ ] **Step 1: 타입을 추가한다**

`frontend/src/lib/types.ts`에 기존 타입들과 같은 스타일로 추가한다:

```ts
export interface Policy {
  tool: string;
  max_nodes: number;
  procs_per_node: number;
  queue: string;
  default_priority: string;
  max_priority: string;
  preview_timeout_seconds: number | null;
  execution_timeout_seconds: number;
  enabled: number;
  updated_at: string;
  updated_by: string;
}

export interface DenyEntry {
  subject_type: string;
  subject: string;
  reason: string | null;
}

export interface ControlState {
  maintenance: number;
  drain: number;
  reason: string | null;
  changed_by: string | null;
  changed_at: string | null;
}
```

- [ ] **Step 2: reason 코드 메시지를 추가한다**

`frontend/src/lib/api.ts`의 `REASON_MESSAGES`에 추가한다:

```ts
  maintenance_mode: "유지보수 중입니다 — 새 작업 제출이 일시 중단되었습니다",
  invalid_policy: "정책 값이 올바르지 않습니다",
  invalid_denylist_subject_type: "대상 유형이 올바르지 않습니다",
  policy_not_found: "정책을 찾을 수 없습니다",
```

- [ ] **Step 3: 훅 3파일을 만든다**

`frontend/src/features/policies/usePolicies.ts`:

```ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "../../lib/api";
import type { Policy } from "../../lib/types";
export const usePolicies = () =>
  useQuery({ queryKey: ["policies"], queryFn: () => apiGet<Policy[]>("/api/admin/policies") });
export interface PolicyBody {
  max_nodes: number; procs_per_node: number; queue: string;
  default_priority: string; max_priority: string;
  preview_timeout_seconds: number | null; execution_timeout_seconds: number;
  enabled: boolean;
}
export const useUpsertPolicy = () => {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (v: { tool: string; body: PolicyBody }) =>
    apiSend("PUT", `/api/admin/policies/${v.tool}`, v.body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["policies"] }) });
};
```

`frontend/src/features/denylist/useDenylist.ts`:

```ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "../../lib/api";
import type { DenyEntry } from "../../lib/types";
export const useDenylist = () =>
  useQuery({ queryKey: ["denylist"], queryFn: () => apiGet<DenyEntry[]>("/api/admin/identity-denylist") });
export const useDeny = () => {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (v: { subject_type: string; subject: string; reason: string | null }) =>
    apiSend("PUT", `/api/admin/identity-denylist/${v.subject_type}/${v.subject}`, { reason: v.reason }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["denylist"] }) });
};
export const useAllow = () => {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (v: { subject_type: string; subject: string }) =>
    apiSend("DELETE", `/api/admin/identity-denylist/${v.subject_type}/${v.subject}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["denylist"] }) });
};
```

`frontend/src/features/control/useControlState.ts`:

```ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "../../lib/api";
import type { ControlState } from "../../lib/types";
export const useControlState = () =>
  useQuery({ queryKey: ["control-state"], queryFn: () => apiGet<ControlState>("/api/admin/control-state") });
export interface ControlStateBody { maintenance: boolean; drain: boolean; reason: string | null; }
export const useSetControlState = () => {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (b: ControlStateBody) =>
    apiSend("PUT", "/api/admin/control-state", b),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["control-state"] }) });
};
```

- [ ] **Step 4: 훅 테스트를 쓴다**

`frontend/src/features/policies/usePolicies.test.tsx` — `frontend/src/features/storages/useStorages.test.tsx`의 구조(renderHook + MSW + QueryClientProvider wrapper)를 그대로 따른다. 검증: `usePolicies`가 목록을 반환하고, `useUpsertPolicy`가 `/api/admin/policies/scan`으로 PUT하며 body가 그대로 전달된다.

- [ ] **Step 5: 테스트와 타입체크를 돌린다**

Run: `cd frontend && npx vitest run src/features/policies/usePolicies.test.tsx && npx tsc -b`
Expected: PASS, tsc 종료코드 0

- [ ] **Step 6: 커밋**

```bash
git add frontend/src/lib/types.ts frontend/src/lib/api.ts frontend/src/features/policies frontend/src/features/denylist frontend/src/features/control
git commit -m "feat(portal): ops console types, reason codes, and hooks"
```

---

### Task 6: 정책 화면 (PoliciesList + PolicyDialog)

**Files:**
- Create: `frontend/src/features/policies/PolicyDialog.tsx`
- Create: `frontend/src/features/policies/PoliciesList.tsx`
- Test: `frontend/src/features/policies/PoliciesList.test.tsx` (신규)

**Interfaces:**
- Consumes: `usePolicies`·`useUpsertPolicy`·`PolicyBody` (Task 5), `Table`(`frontend/src/components/ui/Table.tsx`), `Dialog`, `Button`, `ApiError`
- Produces: `PoliciesList` (Task 9의 라우트가 마운트)

- [ ] **Step 1: 타임아웃 표시 헬퍼와 다이얼로그를 만든다**

`PolicyDialog.tsx` — `StorageDialog.tsx`(`frontend/src/features/storages/StorageDialog.tsx`)의 구조를 그대로 따른다: `open` 상태, 열릴 때 `useEffect`로 폼 재시드, 닫을 때 mutation `reset()`, `m.isError` 인라인 표시, 취소/저장 버튼.

- `tool`은 표시만 하고 편집 불가.
- 숫자 필드: 최대 노드(`max_nodes`), 노드당 프로세스(`procs_per_node`), 실행 타임아웃 초(`execution_timeout_seconds`).
- 미리보기 타임아웃(`preview_timeout_seconds`): 빈 문자열이면 `null`을 보낸다.
- 큐(`queue`) 텍스트, 기본 우선순위·최대 우선순위는 `<select>`로 `low`/`mid`/`high`.
- 활성 체크박스(`enabled`).
- 저장 시 `useUpsertPolicy().mutate({ tool, body }, { onSuccess: () => setOpen(false) })`.
- 각 입력에 `aria-label`을 준다(테스트가 이름으로 찾는다): "최대 노드", "노드당 프로세스", "큐", "기본 우선순위", "최대 우선순위", "미리보기 타임아웃(초)", "실행 타임아웃(초)".

- [ ] **Step 2: 목록 화면을 만든다**

`PoliciesList.tsx` — `StoragesList.tsx`의 레이아웃을 따른다(제목 + `Table`). 컬럼: 도구 / 최대 노드 / 노드당 프로세스 / 큐 / 기본·최대 우선순위 / 미리보기 타임아웃 / 실행 타임아웃 / 활성 / 작업(수정 버튼 → `PolicyDialog`).

타임아웃은 초와 사람이 읽는 형식을 병기한다. 파일 안에 작은 헬퍼를 둔다:

```tsx
function humanSeconds(s: number | null): string {
  if (s === null) return "—";
  if (s % 86400 === 0) return `${s}s (${s / 86400}d)`;
  if (s % 3600 === 0) return `${s}s (${s / 3600}h)`;
  if (s % 60 === 0) return `${s}s (${s / 60}m)`;
  return `${s}s`;
}
```

로딩 중에는 "불러오는 중…", 에러면 `(q.error as ApiError).message`를 표시한다.

- [ ] **Step 3: 테스트를 쓴다**

`PoliciesList.test.tsx` — `StoragesList.test.tsx`의 MSW 구조를 따른다. 최소 3개:

1. 목록 렌더 — 4개 도구 픽스처를 주고 `scan`·`dsync` 행과 `3600s (1h)`·`259200s (3d)` 표기가 보인다.
2. 수정 저장 — "수정" 클릭 → 최대 노드를 바꾸고 "저장" → PUT `/api/admin/policies/:tool` body가 폼 값과 일치한다(`capturedBody`로 검증).
3. 422 표시 — PUT이 `{detail:"invalid_priority"}` 422를 주면 인라인 메시지가 보인다.

미리보기 타임아웃을 비웠을 때 body의 `preview_timeout_seconds`가 `null`인지 확인하는 단언도 2번 테스트에 포함한다.

- [ ] **Step 4: 테스트와 타입체크를 돌린다**

Run: `cd frontend && npx vitest run src/features/policies && npx tsc -b`
Expected: PASS, tsc 0

- [ ] **Step 5: 커밋**

```bash
git add frontend/src/features/policies
git commit -m "feat(portal): policies screen with per-tool edit dialog"
```

---

### Task 7: denylist 화면 (DenylistList + DenyDialog)

**Files:**
- Create: `frontend/src/features/denylist/DenyDialog.tsx`
- Create: `frontend/src/features/denylist/DenylistList.tsx`
- Test: `frontend/src/features/denylist/DenylistList.test.tsx` (신규)

**Interfaces:**
- Consumes: `useDenylist`·`useDeny`·`useAllow` (Task 5), `Table`·`Dialog`·`Button`·`ApiError`
- Produces: `DenylistList` (Task 9의 라우트가 마운트)

- [ ] **Step 1: 추가 다이얼로그를 만든다**

`DenyDialog.tsx` — `StorageDialog.tsx` 패턴. 필드: 대상 유형 `<select>`(`requester`/`owner`/`group`, aria-label "대상 유형"), 대상(aria-label "대상"), 사유(aria-label "사유", 비우면 `null`). 저장 시 `useDeny().mutate({...}, { onSuccess: () => setOpen(false) })`. 닫을 때 `reset()`. 대상은 서버가 소문자로 정규화한다는 안내 문구를 폼에 한 줄 넣는다.

- [ ] **Step 2: 목록 화면을 만든다**

`DenylistList.tsx` — 제목 "denylist" + "대상 추가" 버튼(→ `DenyDialog`) + `Table`(대상 유형 / 대상 / 사유 / 작업). 행별 "해제" 버튼은 `StoragesList.tsx`의 `DeleteButton`과 같은 중첩 확인 다이얼로그 패턴을 쓴다(트리거 "해제", 확인 버튼 "해제 확인" → `useAllow().mutate({subject_type, subject})`), 닫을 때 `reset()`. 목록이 비면 "등재된 대상이 없습니다"를 보여준다.

- [ ] **Step 3: 테스트를 쓴다**

`DenylistList.test.tsx`, 최소 3개:

1. 목록 렌더 — 픽스처 2행이 보이고 "대상 추가" 버튼이 있다.
2. 추가 — "대상 추가" → 유형 `group`, 대상 `wheel`, 사유 입력 → 저장 → PUT `/api/admin/identity-denylist/group/wheel` body `{reason: "..."}`.
3. 해제 — "해제" → "해제 확인" → DELETE `/api/admin/identity-denylist/:type/:subject` 호출.

- [ ] **Step 4: 테스트와 타입체크를 돌린다**

Run: `cd frontend && npx vitest run src/features/denylist && npx tsc -b`
Expected: PASS, tsc 0

- [ ] **Step 5: 커밋**

```bash
git add frontend/src/features/denylist
git commit -m "feat(portal): identity denylist screen"
```

---

### Task 8: 컨트롤 상태 화면

**Files:**
- Create: `frontend/src/features/control/ControlStatePage.tsx`
- Test: `frontend/src/features/control/ControlStatePage.test.tsx` (신규)

**Interfaces:**
- Consumes: `useControlState`·`useSetControlState`·`ControlStateBody` (Task 5), `Card`(`frontend/src/components/ui/Card.tsx`), `Button`, `ApiError`
- Produces: `ControlStatePage` (Task 9의 라우트가 마운트)

- [ ] **Step 1: 화면을 만든다**

제목 "컨트롤 상태". 서버 값이 로드되면 로컬 폼 상태로 시드한다(`useEffect`로 `q.data` 변화 시 재시드 — 슬라이스 3에서 겪은 stale 폼 문제를 반복하지 않는다).

- 체크박스 2개: "유지보수"(aria-label "유지보수"), "드레인"(aria-label "드레인").
- 사유 입력(aria-label "사유"), 비우면 `null`.
- "저장" 버튼 → `useSetControlState().mutate({maintenance, drain, reason})`.
- 현재 상태 요약: 변경자(`changed_by`)·변경 시각(`changed_at`).
- **경고 배너**: 서버 상태 기준으로, `maintenance`가 켜져 있으면 "유지보수 중 — 새 작업 제출이 차단됩니다", `drain`이 켜져 있으면 "드레인 중 — 진행 중인 작업이 더 전진하지 않습니다"를 눈에 띄게(`text-bad` 계열) 표시한다. 둘 다 꺼져 있으면 배너를 렌더하지 않는다.
- mutation 에러는 인라인 표시.

- [ ] **Step 2: 테스트를 쓴다**

`ControlStatePage.test.tsx`, 최소 3개:

1. 현재 상태 렌더 — `{maintenance:1, drain:0, reason:"점검", changed_by:"ops", changed_at:"2026-08-05T00:00:00Z"}`를 주면 유지보수 경고 배너와 `ops`가 보이고, 드레인 배너는 **보이지 않는다**.
2. 저장 body — 드레인을 켜고 "저장" → PUT `/api/admin/control-state` body가 `{maintenance:true, drain:true, reason:"점검"}`이다.
3. 배너 없음 — 둘 다 0이면 어떤 경고 배너도 렌더되지 않는다.

- [ ] **Step 3: 테스트와 타입체크를 돌린다**

Run: `cd frontend && npx vitest run src/features/control && npx tsc -b`
Expected: PASS, tsc 0

- [ ] **Step 4: 커밋**

```bash
git add frontend/src/features/control
git commit -m "feat(portal): control state screen (maintenance/drain)"
```

---

### Task 9: 라우트 · 내비 배선

**Files:**
- Modify: `frontend/src/app/router.tsx`
- Modify: `frontend/src/app/AppShell.tsx`
- Modify: `frontend/src/app/router.test.tsx`

**Interfaces:**
- Consumes: `PoliciesList`(Task 6), `DenylistList`(Task 7), `ControlStatePage`(Task 8), `RequireRole`, `AppShell`
- Produces: `/admin/policies`, `/admin/denylist`, `/admin/control` 경로와 admin 내비 링크

- [ ] **Step 1: 라우트를 추가한다**

`router.tsx`에서 `/admin/audit` 라우트 **다음**, 캐치올(`path="*"`) **앞**에 추가한다(캐치올은 반드시 마지막이어야 한다):

```tsx
        <Route path="/admin/policies" element={<RequireRole role="admin"><AppShell><PoliciesList /></AppShell></RequireRole>} />
        <Route path="/admin/denylist" element={<RequireRole role="admin"><AppShell><DenylistList /></AppShell></RequireRole>} />
        <Route path="/admin/control" element={<RequireRole role="admin"><AppShell><ControlStatePage /></AppShell></RequireRole>} />
```

import 3줄도 기존 import 블록 끝에 추가한다.

- [ ] **Step 2: 내비를 추가한다**

`AppShell.tsx`의 "감사 로그" 링크 다음에:

```tsx
        {isAdmin && <NavLink to="/admin/policies" className={linkCls}>정책</NavLink>}
        {isAdmin && <NavLink to="/admin/denylist" className={linkCls}>denylist</NavLink>}
        {isAdmin && <NavLink to="/admin/control" className={linkCls}>컨트롤 상태</NavLink>}
```

- [ ] **Step 3: 라우터 테스트를 확장한다**

`frontend/src/app/router.test.tsx`의 기존 admin 라우트 테스트(`/admin/audit`를 검증하는 것)와 같은 형태로, admin 세션에서 `/admin/policies`·`/admin/denylist`·`/admin/control`이 각각 렌더되는지 확인하는 케이스를 추가한다. 필요한 MSW 핸들러(`/api/auth/me`와 각 화면의 GET)를 함께 등록한다.

- [ ] **Step 4: 전체 프론트 테스트와 타입체크를 돌린다**

Run: `cd frontend && npm test && npx tsc -b`
Expected: 전부 PASS, tsc 0

- [ ] **Step 5: 커밋**

```bash
git add frontend/src/app
git commit -m "feat(portal): ops console nav + routes"
```
