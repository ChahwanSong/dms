# DMS Phase 2 — 노드 에이전트 + Reconciler + Controller Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 노드 에이전트(마운트/도구/identity/OS 메트릭 프로브 → API POST → 응답으로 설정 수신), 에이전트 리포트 수집·조회 API, storage-reconciler와 retention 루프, 그리고 이 루프들을 품는 `dms controller` 숙주를 만든다.

**Architecture:** 스펙 §6(스토리지와 에이전트)·§3(dms-controller) 구현. 에이전트는 DB를 모르는 HTTP 클라이언트이고, 설정(스토리지 목록·프로브 대상)은 리포트 POST의 **응답**으로 받는다. controller는 재시작 가능한 `run_once()` 루프들의 숙주이며 리더 리스로 다중 replica에 안전하다. 신선도는 저장하지 않고 읽는 시점에 판정한다.

**Tech Stack:** Phase 1 코드 위에 Python 3.11+, FastAPI, httpx(에이전트 HTTP 클라이언트, 런타임 의존성으로 승격), stdlib(pwd/grp/shutil/os.statvfs).

## Global Constraints

- 스펙이 진실: `docs/superpowers/specs/2026-08-02-dms-clean-slate-design.md` §3, §6. legacy 코드 재사용 금지 (읽기 전용 참고만).
- 모든 런타임 SQL은 `src/dms/repositories/` 안에만. named param `:name`, SQLite/PG 호환.
- 에이전트 프로브는 항목별 **fail-soft** (한 프로브 실패가 리포트 전체를 죽이지 않고 reason으로 기록), 수집·게이트는 **fail-closed**.
- 신선도는 저장하지 않는다 — `reported_at`을 읽는 시점에 stale 임계(기본 300초)와 비교.
- `agent_reports`(이력) INSERT + `agent_nodes`(최신 1행) upsert는 **한 트랜잭션**.
- 에이전트 수집 actor는 정확히 `node:{node_name}` — 불일치 시 403 `agent_node_identity_mismatch`.
- 사유 코드는 snake_case. 시각은 `dms.db.utc_now_iso()` 형식(UTC ISO-8601 `...Z`). JSON 컬럼은 TEXT + `dump_json`/`load_json`.
- 전체 테스트는 서비스 없이 SQLite로 돈다 (`pytest` 단독). HTTP는 `httpx.MockTransport`, 파일시스템/시스템 콜은 주입으로 스텁.
- pytest는 filterwarnings=error 상태 — 새 warning은 에러다.
- 커밋: conventional commit + `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>` 트레일러, 태스크마다 커밋.

## Phase 1이 제공하는 인터페이스 (이 플랜의 전제 — 변경 금지)

- `dms.db`: `Database.connect(url)`, `.execute(sql, params)`, `.query`, `.query_one`, `.transaction()`(블록 전체 RLock), `.dialect`; `utc_now_iso()`, `dump_json(v)`, `load_json(t)`.
- `dms.repositories.Repositories(db)` → `.requests` `.storages` `.accounts` `.control` `.db`.
- `StoragesRepository`: `.list() -> list[dict]`(컬럼: storage_name, mount_path, managed_root, backend_type, enabled(0/1), status, status_detail, ...), `.get(name)`, `.set_status(storage_name, status, detail=None)`.
- `ControlRepository`: `.try_acquire_lease(component, holder, lease_seconds, now_iso=None) -> bool`, 내부 `_iso_plus(now_iso, seconds)`.
- `dms.config.Settings`(frozen dataclass) / `SettingsError(problems)` / `_is_placeholder(value)`.
- `dms.api.app.create_app(settings, db)`; `dms.api.auth`: `Identity(actor, role)`, `require_user`, `require_admin`, `tokens_match`.
- `dms.cli.main(argv)` — argparse, 서브커맨드 migrate/api (Settings 로딩이 파싱 뒤).
- 스키마(이미 존재): `agent_reports(id, node_name, report, reported_at)`, `agent_nodes(node_name PK, report, reported_at)`, `identity_probe_targets(username PK, last_requested_at)`, `component_leases`.
- tests/conftest.py: `db`(tmp sqlite+migrate), `settings`, `client`(TestClient) 픽스처.

## File Structure

```
src/dms/db.py                       # (수정) iso_plus(ts, seconds) 공개 헬퍼 추가
src/dms/repositories/agents.py      # AgentsRepository: ingest/list_nodes/node_reports/fresh_reports/prune_reports
src/dms/repositories/control.py     # (수정) register_probe_target/probe_targets, _iso_plus → db.iso_plus 사용
src/dms/repositories/__init__.py    # (수정) .agents 연결
src/dms/config.py                   # (수정) 서버 Settings knob 추가 + AgentSettings
src/dms/agent/__init__.py
src/dms/agent/probes.py             # parse_mountinfo, probe_mounts/tools/identities/os_metrics (전부 주입 가능)
src/dms/agent/runner.py             # build_report, AgentRunner(run_once), run_loop
src/dms/api/routes_agent.py         # POST /api/agent/report
src/dms/api/routes_nodes.py         # GET /api/admin/nodes, GET /api/admin/nodes/{name}/reports
src/dms/api/app.py                  # (수정) 라우터 2개 마운트
src/dms/reconciler.py               # reconcile_storages_once
src/dms/retention.py                # prune_agent_reports_once
src/dms/controller.py               # Loop, build_loops, run_all_once, run_forever
src/dms/cli.py                      # (수정) controller/agent 서브커맨드
tests/test_repo_agents.py
tests/test_repo_probe_targets.py
tests/test_config_phase2.py
tests/test_agent_probes.py
tests/test_agent_runner.py
tests/test_api_agent.py
tests/test_api_nodes.py
tests/test_reconciler.py
tests/test_retention.py
tests/test_controller.py
```

---

### Task 1: 에이전트 리포트 저장소 (`repositories/agents.py`) + `iso_plus` 공개화

**Files:**
- Modify: `src/dms/db.py` (iso_plus 추가)
- Modify: `src/dms/repositories/control.py` (`_iso_plus` 제거, `from ..db import iso_plus` 사용)
- Create: `src/dms/repositories/agents.py`
- Modify: `src/dms/repositories/__init__.py` (`self.agents = AgentsRepository(db)`)
- Test: `tests/test_repo_agents.py`

**Interfaces:**
- Consumes: `Database`, `dump_json/load_json/utc_now_iso`, 기존 스키마.
- Produces:
  - `dms.db.iso_plus(ts: str, seconds: int) -> str` — ISO-8601 `...Z` 문자열에 초 더하기(음수 허용)
  - `AgentsRepository(db)`:
    - `ingest(node_name: str, report: dict, reported_at: str | None = None) -> None` — 한 트랜잭션에서 `agent_reports` INSERT + `agent_nodes` upsert(DELETE+INSERT)
    - `list_nodes(*, stale_seconds: int, now_iso: str | None = None) -> list[dict]` — 노드별 `{node_name, reported_at, fresh: bool, report: dict}`, node_name 오름차순. `fresh = reported_at > iso_plus(now, -stale_seconds)`
    - `fresh_reports(*, stale_seconds: int, now_iso: str | None = None) -> list[dict]` — fresh인 것만
    - `node_reports(node_name: str, *, limit: int = 200) -> list[dict]` — `{reported_at, report}` 최신순(id DESC)
    - `prune_reports(cutoff_iso: str, batch_size: int = 5000) -> int` — `reported_at < cutoff` 행을 배치 삭제, 삭제 총수 반환. **`agent_nodes`는 건드리지 않는다**

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/test_repo_agents.py
from dms.db import iso_plus
from dms.repositories.agents import AgentsRepository

REPORT = {"mounts": [], "tools": [], "identities": [], "os": {}}


def test_iso_plus_handles_negative_and_boundaries():
    assert iso_plus("2026-08-02T00:00:10Z", -20) == "2026-08-01T23:59:50Z"
    assert iso_plus("2026-08-02T23:59:50Z", 20) == "2026-08-03T00:00:10Z"


def test_ingest_writes_history_and_current(db):
    repo = AgentsRepository(db)
    repo.ingest("node-a", REPORT, reported_at="2026-08-02T10:00:00Z")
    repo.ingest("node-a", REPORT, reported_at="2026-08-02T10:01:00Z")
    history = db.query("SELECT node_name, reported_at FROM agent_reports ORDER BY id")
    assert [h["reported_at"] for h in history] == [
        "2026-08-02T10:00:00Z", "2026-08-02T10:01:00Z"]
    current = db.query("SELECT node_name, reported_at FROM agent_nodes")
    assert current == [{"node_name": "node-a", "reported_at": "2026-08-02T10:01:00Z"}]


def test_list_nodes_computes_freshness_at_read_time(db):
    repo = AgentsRepository(db)
    repo.ingest("node-a", REPORT, reported_at="2026-08-02T10:00:00Z")
    repo.ingest("node-b", REPORT, reported_at="2026-08-02T09:00:00Z")
    nodes = repo.list_nodes(stale_seconds=300, now_iso="2026-08-02T10:04:00Z")
    assert [(n["node_name"], n["fresh"]) for n in nodes] == [
        ("node-a", True), ("node-b", False)]
    assert nodes[0]["report"] == REPORT
    fresh = repo.fresh_reports(stale_seconds=300, now_iso="2026-08-02T10:04:00Z")
    assert [n["node_name"] for n in fresh] == ["node-a"]


def test_node_reports_newest_first_with_limit(db):
    repo = AgentsRepository(db)
    for minute in (0, 1, 2):
        repo.ingest("node-a", {"seq": minute},
                    reported_at=f"2026-08-02T10:0{minute}:00Z")
    rows = repo.node_reports("node-a", limit=2)
    assert [r["report"]["seq"] for r in rows] == [2, 1]


def test_prune_reports_keeps_current_and_recent(db):
    repo = AgentsRepository(db)
    repo.ingest("node-a", REPORT, reported_at="2026-08-01T00:00:00Z")
    repo.ingest("node-a", REPORT, reported_at="2026-08-02T10:00:00Z")
    deleted = repo.prune_reports("2026-08-02T00:00:00Z", batch_size=1)
    assert deleted == 1
    assert len(db.query("SELECT id FROM agent_reports")) == 1
    assert db.query_one("SELECT reported_at FROM agent_nodes WHERE node_name = 'node-a'")[
        "reported_at"] == "2026-08-02T10:00:00Z"
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_repo_agents.py -v`
Expected: FAIL — `ImportError: cannot import name 'iso_plus'`

- [ ] **Step 3: 구현**

`src/dms/db.py`에 추가 (utc_now_iso 아래):

```python
def iso_plus(ts: str, seconds: int) -> str:
    """ISO-8601 UTC(...Z) 문자열에 초를 더한다(음수 허용)."""
    base = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return (base + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")
```

(`from datetime import datetime, timedelta, timezone`으로 상단 import 갱신.)

`src/dms/repositories/control.py`: 모듈 상단 `from datetime import ...`와 `_iso_plus` 함수를 제거하고 `from ..db import ... iso_plus`로 교체, `try_acquire_lease`의 `_iso_plus(now, lease_seconds)` 호출을 `iso_plus(now, lease_seconds)`로 변경.

```python
# src/dms/repositories/agents.py
"""에이전트 리포트 저장소: 이력(agent_reports) + 노드별 최신 1행(agent_nodes)."""
from ..db import Database, dump_json, iso_plus, load_json, utc_now_iso


class AgentsRepository:
    def __init__(self, db: Database):
        self._db = db

    def ingest(self, node_name: str, report: dict, reported_at: str | None = None) -> None:
        at = reported_at or utc_now_iso()
        payload = dump_json(report)
        with self._db.transaction():
            self._db.execute(
                """INSERT INTO agent_reports (node_name, report, reported_at)
                   VALUES (:n, :r, :at)""",
                {"n": node_name, "r": payload, "at": at})
            self._db.execute("DELETE FROM agent_nodes WHERE node_name = :n",
                             {"n": node_name})
            self._db.execute(
                """INSERT INTO agent_nodes (node_name, report, reported_at)
                   VALUES (:n, :r, :at)""",
                {"n": node_name, "r": payload, "at": at})

    def list_nodes(self, *, stale_seconds: int, now_iso: str | None = None) -> list[dict]:
        now = now_iso or utc_now_iso()
        threshold = iso_plus(now, -stale_seconds)
        rows = self._db.query(
            "SELECT node_name, report, reported_at FROM agent_nodes ORDER BY node_name")
        return [{
            "node_name": row["node_name"],
            "reported_at": row["reported_at"],
            "fresh": row["reported_at"] > threshold,
            "report": load_json(row["report"]),
        } for row in rows]

    def fresh_reports(self, *, stale_seconds: int, now_iso: str | None = None) -> list[dict]:
        return [n for n in self.list_nodes(stale_seconds=stale_seconds, now_iso=now_iso)
                if n["fresh"]]

    def node_reports(self, node_name: str, *, limit: int = 200) -> list[dict]:
        rows = self._db.query(
            """SELECT report, reported_at FROM agent_reports
               WHERE node_name = :n ORDER BY id DESC LIMIT :limit""",
            {"n": node_name, "limit": limit})
        return [{"reported_at": r["reported_at"], "report": load_json(r["report"])}
                for r in rows]

    def prune_reports(self, cutoff_iso: str, batch_size: int = 5000) -> int:
        total = 0
        while True:
            with self._db.transaction():
                rows = self._db.query(
                    """SELECT id FROM agent_reports WHERE reported_at < :cutoff
                       ORDER BY id LIMIT :n""",
                    {"cutoff": cutoff_iso, "n": batch_size})
                if not rows:
                    return total
                placeholders = ", ".join(f":i{k}" for k in range(len(rows)))
                params = {f"i{k}": row["id"] for k, row in enumerate(rows)}
                self._db.execute(
                    f"DELETE FROM agent_reports WHERE id IN ({placeholders})", params)
                total += len(rows)
```

`src/dms/repositories/__init__.py`: `from .agents import AgentsRepository` + `self.agents = AgentsRepository(db)` 추가.

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/pytest tests/test_repo_agents.py tests/test_repo_control.py -v` 후 `.venv/bin/pytest -q`
Expected: 전체 PASS (control.py 리팩터 회귀 포함 확인)

- [ ] **Step 5: Commit**

```bash
git add src/dms/db.py src/dms/repositories/ tests/test_repo_agents.py
git commit -m "feat: 에이전트 리포트 저장소 (이력+최신, 읽기 시점 신선도, prune)"
```

---

### Task 2: identity 프로브 대상 (`control.py` 확장)

**Files:**
- Modify: `src/dms/repositories/control.py`
- Test: `tests/test_repo_probe_targets.py`

**Interfaces:**
- Consumes: `identity_probe_targets` 테이블(username PK, last_requested_at), `iso_plus`.
- Produces:
  - `ControlRepository.register_probe_target(username: str, now_iso: str | None = None) -> None` — upsert(last_requested_at 갱신)
  - `ControlRepository.probe_targets(*, ttl_seconds: int, now_iso: str | None = None) -> list[str]` — 만료 행 삭제 후 남은 username 오름차순

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/test_repo_probe_targets.py
from dms.repositories.control import ControlRepository


def test_register_and_list(db):
    repo = ControlRepository(db)
    repo.register_probe_target("alice", now_iso="2026-08-02T10:00:00Z")
    repo.register_probe_target("bob", now_iso="2026-08-02T10:30:00Z")
    assert repo.probe_targets(ttl_seconds=3600, now_iso="2026-08-02T10:40:00Z") == [
        "alice", "bob"]


def test_expired_targets_are_dropped(db):
    repo = ControlRepository(db)
    repo.register_probe_target("old", now_iso="2026-08-02T08:00:00Z")
    repo.register_probe_target("new", now_iso="2026-08-02T10:00:00Z")
    assert repo.probe_targets(ttl_seconds=3600, now_iso="2026-08-02T10:30:00Z") == ["new"]
    assert db.query("SELECT username FROM identity_probe_targets") == [
        {"username": "new"}]


def test_reregister_refreshes_ttl(db):
    repo = ControlRepository(db)
    repo.register_probe_target("alice", now_iso="2026-08-02T08:00:00Z")
    repo.register_probe_target("alice", now_iso="2026-08-02T10:00:00Z")
    assert repo.probe_targets(ttl_seconds=3600, now_iso="2026-08-02T10:30:00Z") == ["alice"]
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_repo_probe_targets.py -v`
Expected: FAIL — `AttributeError: ... register_probe_target`

- [ ] **Step 3: 구현 (control.py에 추가)**

```python
    # --- identity probe targets ---
    def register_probe_target(self, username: str, now_iso: str | None = None) -> None:
        now = now_iso or utc_now_iso()
        with self._db.transaction():
            self._db.execute(
                "DELETE FROM identity_probe_targets WHERE username = :u", {"u": username})
            self._db.execute(
                """INSERT INTO identity_probe_targets (username, last_requested_at)
                   VALUES (:u, :at)""",
                {"u": username, "at": now})

    def probe_targets(self, *, ttl_seconds: int, now_iso: str | None = None) -> list[str]:
        now = now_iso or utc_now_iso()
        cutoff = iso_plus(now, -ttl_seconds)
        with self._db.transaction():
            self._db.execute(
                "DELETE FROM identity_probe_targets WHERE last_requested_at < :cutoff",
                {"cutoff": cutoff})
            rows = self._db.query(
                "SELECT username FROM identity_probe_targets ORDER BY username")
        return [r["username"] for r in rows]
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/pytest tests/test_repo_probe_targets.py -v` 후 `.venv/bin/pytest -q`
Expected: 전체 PASS

- [ ] **Step 5: Commit**

```bash
git add src/dms/repositories/control.py tests/test_repo_probe_targets.py
git commit -m "feat: identity 프로브 대상 등록/조회 (TTL 정리 포함)"
```

---

### Task 3: 설정 확장 (`config.py`)

**Files:**
- Modify: `src/dms/config.py`
- Test: `tests/test_config_phase2.py`

**Interfaces:**
- Consumes: 기존 `Settings`, `SettingsError`, `_is_placeholder`.
- Produces:
  - `Settings`에 int 필드 5개 추가 (기본값 포함, env는 선택): `agent_report_stale_seconds=300`(`DMS_AGENT_REPORT_STALE_SECONDS`), `agent_report_interval_seconds=60`(`DMS_AGENT_REPORT_INTERVAL_SECONDS`), `reconcile_interval_seconds=30`(`DMS_RECONCILE_INTERVAL_SECONDS`), `retention_interval_seconds=3600`(`DMS_RETENTION_INTERVAL_SECONDS`), `agent_report_retention_days=30`(`DMS_AGENT_REPORT_RETENTION_DAYS`), `identity_probe_ttl_seconds=3600`(`DMS_IDENTITY_PROBE_TTL_SECONDS`) — 비정수는 문제 목록에 수집
  - `@dataclass(frozen=True) AgentSettings`: `api_url: str`, `shared_token: str`, `node_name: str`, `interval_seconds: int = 60`, `mountinfo_path: str = "/proc/1/mountinfo"`
  - `AgentSettings.from_env(environ) -> AgentSettings` — 필수: `DMS_AGENT_API_URL`, `DMS_SHARED_TOKEN`(placeholder 가드 동일 적용); `DMS_AGENT_NODE_NAME` 없으면 `socket.gethostname()`; `DMS_AGENT_INTERVAL_SECONDS`, `DMS_AGENT_MOUNTINFO_PATH` 선택
  - `AGENT_TOOL_NAMES = ("dscan", "dsync", "nsync", "drm")` 모듈 상수

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/test_config_phase2.py
import pytest
from dms.config import AGENT_TOOL_NAMES, AgentSettings, Settings, SettingsError

VALID = {
    "DMS_DATABASE_URL": "sqlite:///tmp/dms.db",
    "DMS_SHARED_TOKEN": "tok",
    "DMS_ADMIN_TOKEN": "adm",
    "DMS_SESSION_SECRET": "sess",
}


def test_server_phase2_defaults_and_overrides():
    s = Settings.from_env(VALID)
    assert s.agent_report_stale_seconds == 300
    assert s.agent_report_interval_seconds == 60
    assert s.reconcile_interval_seconds == 30
    assert s.retention_interval_seconds == 3600
    assert s.agent_report_retention_days == 30
    assert s.identity_probe_ttl_seconds == 3600
    s2 = Settings.from_env({**VALID, "DMS_RECONCILE_INTERVAL_SECONDS": "5"})
    assert s2.reconcile_interval_seconds == 5
    with pytest.raises(SettingsError) as e:
        Settings.from_env({**VALID, "DMS_AGENT_REPORT_STALE_SECONDS": "soon"})
    assert "DMS_AGENT_REPORT_STALE_SECONDS" in str(e.value)


def test_agent_settings_required_and_defaults(monkeypatch):
    env = {"DMS_AGENT_API_URL": "http://dms-api:8080", "DMS_SHARED_TOKEN": "tok"}
    s = AgentSettings.from_env(env)
    assert s.api_url == "http://dms-api:8080"
    assert s.interval_seconds == 60
    assert s.mountinfo_path == "/proc/1/mountinfo"
    assert s.node_name  # hostname fallback은 비어있지 않다
    s2 = AgentSettings.from_env({**env, "DMS_AGENT_NODE_NAME": "node-7",
                                 "DMS_AGENT_INTERVAL_SECONDS": "10"})
    assert s2.node_name == "node-7" and s2.interval_seconds == 10


def test_agent_settings_fail_closed():
    with pytest.raises(SettingsError) as e:
        AgentSettings.from_env({"DMS_AGENT_API_URL": "CHANGE_ME",
                                "DMS_SHARED_TOKEN": ""})
    text = str(e.value)
    assert "DMS_AGENT_API_URL" in text and "DMS_SHARED_TOKEN" in text


def test_tool_names_constant():
    assert AGENT_TOOL_NAMES == ("dscan", "dsync", "nsync", "drm")
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_config_phase2.py -v`
Expected: FAIL — ImportError

- [ ] **Step 3: 구현 (config.py 수정)**

```python
import socket

AGENT_TOOL_NAMES = ("dscan", "dsync", "nsync", "drm")

_SERVER_INT_KEYS = (
    ("DMS_AGENT_REPORT_STALE_SECONDS", "agent_report_stale_seconds", 300),
    ("DMS_AGENT_REPORT_INTERVAL_SECONDS", "agent_report_interval_seconds", 60),
    ("DMS_RECONCILE_INTERVAL_SECONDS", "reconcile_interval_seconds", 30),
    ("DMS_RETENTION_INTERVAL_SECONDS", "retention_interval_seconds", 3600),
    ("DMS_AGENT_REPORT_RETENTION_DAYS", "agent_report_retention_days", 30),
    ("DMS_IDENTITY_PROBE_TTL_SECONDS", "identity_probe_ttl_seconds", 3600),
)


def _parse_int(environ, key, default, problems):
    raw = environ.get(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        problems.append(f"{key} is not an integer: {raw!r}")
        return default
```

`Settings` dataclass에 필드 6개를 **기존 필드 뒤에** 추가 (전부 기본값 있음 — frozen dataclass 필드 순서 규칙 충족):

```python
    api_host: str = "0.0.0.0"          # (기존)
    api_port: int = 8080               # (기존)
    agent_report_stale_seconds: int = 300
    agent_report_interval_seconds: int = 60
    reconcile_interval_seconds: int = 30
    retention_interval_seconds: int = 3600
    agent_report_retention_days: int = 30
    identity_probe_ttl_seconds: int = 3600
```

`from_env`의 기존 로직(필수 4키 + port) 뒤에 추가 수집을 넣고 생성자에 전달:

```python
        extra = {field: _parse_int(environ, env_key, default, problems)
                 for env_key, field, default in _SERVER_INT_KEYS}
        if problems:
            raise SettingsError(problems)
        return cls(..., **extra)   # 기존 키워드 인자들 + extra
```

(`if problems` 검사는 한 곳으로 합쳐 port/추가 int 문제를 전부 모아 보고.)

```python
@dataclass(frozen=True)
class AgentSettings:
    api_url: str
    shared_token: str
    node_name: str
    interval_seconds: int = 60
    mountinfo_path: str = "/proc/1/mountinfo"

    @classmethod
    def from_env(cls, environ: Mapping) -> "AgentSettings":
        problems: list[str] = []
        api_url = environ.get("DMS_AGENT_API_URL")
        token = environ.get("DMS_SHARED_TOKEN")
        if _is_placeholder(api_url):
            problems.append("DMS_AGENT_API_URL is missing or a placeholder")
        if _is_placeholder(token):
            problems.append("DMS_SHARED_TOKEN is missing or a placeholder")
        interval = _parse_int(environ, "DMS_AGENT_INTERVAL_SECONDS", 60, problems)
        if problems:
            raise SettingsError(problems)
        return cls(
            api_url=api_url.rstrip("/"),
            shared_token=token,
            node_name=environ.get("DMS_AGENT_NODE_NAME") or socket.gethostname(),
            interval_seconds=interval,
            mountinfo_path=environ.get("DMS_AGENT_MOUNTINFO_PATH", "/proc/1/mountinfo"),
        )
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/pytest tests/test_config_phase2.py tests/test_config.py -v` 후 `.venv/bin/pytest -q`
Expected: 전체 PASS

- [ ] **Step 5: Commit**

```bash
git add src/dms/config.py tests/test_config_phase2.py
git commit -m "feat: Phase 2 설정 (서버 주기/보존 knob, AgentSettings)"
```

---

### Task 4: 에이전트 프로브 — 마운트 (`agent/probes.py`)

**Files:**
- Create: `src/dms/agent/__init__.py` (빈 파일)
- Create: `src/dms/agent/probes.py`
- Test: `tests/test_agent_probes.py`

**Interfaces:**
- Consumes: 없음 (순수 함수, 시스템 접근은 전부 주입).
- Produces:
  - `parse_mountinfo(text: str) -> set[str]` — mountinfo의 mount point(5번째 필드, 인덱스 4) 집합. 옥탈 이스케이프(`\040`=space, `\011`=tab, `\012`=nl, `\134`=backslash) 복원
  - `probe_mounts(storages: list[dict], *, mountinfo_text: str, isdir=os.path.isdir, access=os.access) -> list[dict]` — storage마다 `{storage_name, mount_path, exists, is_mountpoint, readable, writable, status, reason}`. `status="Ready"` 조건: exists AND is_mountpoint AND readable. 아니면 `"Missing"` + 첫 번째 실패 reason (`missing_mount_path` / `not_a_mountpoint` / `not_readable`). `writable`은 독립 플래그(Ready 조건 아님 — 쓰기 대상 게이트는 Phase 3 소관)

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/test_agent_probes.py
import os
from dms.agent.probes import parse_mountinfo, probe_mounts

MOUNTINFO = """\
22 1 0:20 / / rw,relatime - ext4 /dev/root rw
40 22 0:33 / /mnt/ceph rw,relatime - ceph 10.0.0.1:/ rw
41 22 0:34 / /mnt/with\\040space rw - ext4 /dev/sdb rw
"""

STORAGES = [
    {"storage_name": "ceph-a", "mount_path": "/mnt/ceph"},
    {"storage_name": "gone", "mount_path": "/mnt/gone"},
]


def test_parse_mountinfo_extracts_mountpoints_and_unescapes():
    points = parse_mountinfo(MOUNTINFO)
    assert "/mnt/ceph" in points
    assert "/mnt/with space" in points
    assert "/" in points


def test_probe_mounts_ready_and_missing():
    def isdir(path):
        return path == "/mnt/ceph"

    def access(path, mode):
        return path == "/mnt/ceph"

    out = probe_mounts(STORAGES, mountinfo_text=MOUNTINFO, isdir=isdir, access=access)
    ready = out[0]
    assert ready["storage_name"] == "ceph-a" and ready["status"] == "Ready"
    assert ready["is_mountpoint"] and ready["readable"] and ready["writable"]
    missing = out[1]
    assert missing["status"] == "Missing" and missing["reason"] == "missing_mount_path"


def test_probe_mounts_not_a_mountpoint_and_not_readable():
    out = probe_mounts(
        [{"storage_name": "s", "mount_path": "/plain/dir"}],
        mountinfo_text=MOUNTINFO, isdir=lambda p: True, access=lambda p, m: True)
    assert out[0]["status"] == "Missing" and out[0]["reason"] == "not_a_mountpoint"

    def no_read(path, mode):
        return mode != os.R_OK

    out = probe_mounts(
        [{"storage_name": "s", "mount_path": "/mnt/ceph"}],
        mountinfo_text=MOUNTINFO, isdir=lambda p: True, access=no_read)
    assert out[0]["status"] == "Missing" and out[0]["reason"] == "not_readable"
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_agent_probes.py -v`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: 구현**

```python
# src/dms/agent/probes.py
"""노드 프로브. 시스템 접근(파일/명령)은 전부 파라미터 주입 — 순수 로직만 이 모듈에 둔다."""
import os

_OCTAL_ESCAPES = {"\\040": " ", "\\011": "\t", "\\012": "\n", "\\134": "\\"}


def _unescape(field: str) -> str:
    for escaped, char in _OCTAL_ESCAPES.items():
        field = field.replace(escaped, char)
    return field


def parse_mountinfo(text: str) -> set[str]:
    points: set[str] = set()
    for line in text.splitlines():
        fields = line.split()
        if len(fields) > 4:
            points.add(_unescape(fields[4]))
    return points


def probe_mounts(storages, *, mountinfo_text, isdir=os.path.isdir, access=os.access):
    points = parse_mountinfo(mountinfo_text)
    results = []
    for storage in storages:
        path = storage["mount_path"]
        exists = bool(isdir(path))
        is_mountpoint = path in points
        readable = exists and bool(access(path, os.R_OK)) and bool(access(path, os.X_OK))
        writable = exists and bool(access(path, os.W_OK))
        if not exists:
            status, reason = "Missing", "missing_mount_path"
        elif not is_mountpoint:
            status, reason = "Missing", "not_a_mountpoint"
        elif not readable:
            status, reason = "Missing", "not_readable"
        else:
            status, reason = "Ready", None
        results.append({
            "storage_name": storage["storage_name"], "mount_path": path,
            "exists": exists, "is_mountpoint": is_mountpoint,
            "readable": readable, "writable": writable,
            "status": status, "reason": reason,
        })
    return results
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/pytest tests/test_agent_probes.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/dms/agent/ tests/test_agent_probes.py
git commit -m "feat: 에이전트 마운트 프로브 (mountinfo 파싱, Ready/Missing 판정)"
```

---

### Task 5: 에이전트 프로브 — 도구/identity/OS 메트릭 (`probes.py` 계속)

**Files:**
- Modify: `src/dms/agent/probes.py`
- Test: `tests/test_agent_probes.py`에 추가

**Interfaces:**
- Consumes: Task 4의 모듈.
- Produces:
  - `probe_tools(names, *, which=shutil.which, run=subprocess.run) -> list[dict]` — `{name, status Ready|Missing, path, version, reason}`. which 실패 → `tool_not_found`. `--version` 실행(5초 타임아웃, stdout+stderr 합쳐 첫 줄)은 fail-soft: 실패해도 status는 Ready 유지, `version=None`, reason에 `version_probe_failed:<타입>`
  - `probe_identities(usernames, *, getpwnam=pwd.getpwnam, getgrall=grp.getgrall) -> list[dict]` — `{username, status Ready|Missing, uid, gid, groups}`. KeyError → Missing + `user_not_found`
  - `probe_os_metrics(storages, *, read_text, statvfs=os.statvfs) -> dict` — `{load1, load5, load15, memory_total_kb, memory_available_kb, disks: [{storage_name, total_bytes, used_bytes}], network_rx_bytes, network_tx_bytes}`. `read_text(path) -> str`은 `/proc/loadavg`, `/proc/meminfo`, `/proc/net/dev`를 읽는 주입 함수. 각 섹션 독립 fail-soft(실패 시 해당 키 None/빈 리스트). 네트워크는 lo 제외 인터페이스의 rx/tx bytes 합
- **결정 노트 (스펙 §6 대비 의도적 조정):** 스펙의 OS 메트릭 목록 중 cpu 사용률(%)은 /proc/stat 2회 샘플링 대기가 필요해 에이전트 사이클에 지연을 넣으므로, Phase 2는 같은 정보를 주는 loadavg(1/5/15)로 대신한다. cpu% 추가 여부는 포탈 대시보드 요구가 구체화되는 Phase 5에서 결정 — 이 결정은 리뷰에서 spec-gap이 아니라 승인된 조정으로 다룬다.

- [ ] **Step 1: 실패하는 테스트 작성 (test_agent_probes.py에 추가)**

```python
from dms.agent.probes import probe_identities, probe_os_metrics, probe_tools

LOADAVG = "1.50 0.75 0.30 2/345 6789\n"
MEMINFO = "MemTotal:       16384000 kB\nMemFree:  1000 kB\nMemAvailable:    8192000 kB\n"
NETDEV = """\
Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo:  999999    100    0    0    0     0          0         0   999999    100    0    0    0     0       0          0
  eth0: 1000    10    0    0    0     0          0         0   2000    20    0    0    0     0       0          0
  eth1: 500    5    0    0    0     0          0         0   700    7    0    0    0     0       0          0
"""


def test_probe_tools_found_and_missing():
    def which(name):
        return f"/opt/bin/{name}" if name != "nsync" else None

    class Proc:
        stdout = "dsync 0.12-dms\nextra"
        stderr = ""

    out = probe_tools(["dsync", "nsync"], which=which, run=lambda *a, **k: Proc())
    assert out[0] == {"name": "dsync", "status": "Ready", "path": "/opt/bin/dsync",
                      "version": "dsync 0.12-dms", "reason": None}
    assert out[1]["status"] == "Missing" and out[1]["reason"] == "tool_not_found"


def test_probe_tools_version_failure_is_soft():
    def boom(*a, **k):
        raise OSError("exec failed")

    out = probe_tools(["drm"], which=lambda n: "/opt/bin/drm", run=boom)
    assert out[0]["status"] == "Ready" and out[0]["version"] is None
    assert out[0]["reason"].startswith("version_probe_failed:")


def test_probe_identities():
    class Pw:
        pw_uid, pw_gid = 1000, 1000

    class Gr:
        def __init__(self, name, members):
            self.gr_name, self.gr_mem = name, members

    def getpwnam(name):
        if name == "alice":
            return Pw()
        raise KeyError(name)

    out = probe_identities(["alice", "ghost"], getpwnam=getpwnam,
                           getgrall=lambda: [Gr("dev", ["alice"]), Gr("ops", [])])
    assert out[0] == {"username": "alice", "status": "Ready", "uid": 1000,
                      "gid": 1000, "groups": ["dev"]}
    assert out[1]["status"] == "Missing" and out[1]["reason"] == "user_not_found"


def test_probe_os_metrics_with_failures_are_soft():
    files = {"/proc/loadavg": LOADAVG, "/proc/meminfo": MEMINFO, "/proc/net/dev": NETDEV}

    def read_text(path):
        return files[path]

    class Vfs:
        f_frsize, f_blocks, f_bavail = 4096, 1000, 250

    out = probe_os_metrics([{"storage_name": "s", "mount_path": "/mnt/ceph"}],
                           read_text=read_text, statvfs=lambda p: Vfs())
    assert out["load1"] == 1.50 and out["load15"] == 0.30
    assert out["memory_total_kb"] == 16384000
    assert out["memory_available_kb"] == 8192000
    assert out["disks"] == [{"storage_name": "s", "total_bytes": 4096000,
                             "used_bytes": 3072000}]
    assert out["network_rx_bytes"] == 1500 and out["network_tx_bytes"] == 2700

    def broken(path):
        raise OSError("no proc")

    out = probe_os_metrics([], read_text=broken, statvfs=lambda p: Vfs())
    assert out["load1"] is None and out["memory_total_kb"] is None
    assert out["network_rx_bytes"] is None and out["disks"] == []
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_agent_probes.py -v`
Expected: 새 테스트 FAIL — ImportError

- [ ] **Step 3: 구현 (probes.py에 추가)**

```python
import grp
import pwd
import shutil
import subprocess


def probe_tools(names, *, which=shutil.which, run=subprocess.run):
    results = []
    for name in names:
        path = which(name)
        if not path:
            results.append({"name": name, "status": "Missing", "path": None,
                            "version": None, "reason": "tool_not_found"})
            continue
        version, reason = None, None
        try:
            proc = run([path, "--version"], capture_output=True, text=True, timeout=5)
            first_line = (proc.stdout or proc.stderr or "").splitlines()
            version = first_line[0].strip() if first_line else None
        except Exception as exc:  # fail-soft: 버전 실패가 도구 존재를 부정하지 않는다
            reason = f"version_probe_failed:{type(exc).__name__}"
        results.append({"name": name, "status": "Ready", "path": path,
                        "version": version, "reason": reason})
    return results


def probe_identities(usernames, *, getpwnam=pwd.getpwnam, getgrall=grp.getgrall):
    try:
        all_groups = list(getgrall())
    except Exception:
        all_groups = []
    results = []
    for username in usernames:
        try:
            entry = getpwnam(username)
        except KeyError:
            results.append({"username": username, "status": "Missing",
                            "uid": None, "gid": None, "groups": [],
                            "reason": "user_not_found"})
            continue
        groups = sorted(g.gr_name for g in all_groups if username in g.gr_mem)
        results.append({"username": username, "status": "Ready",
                        "uid": entry.pw_uid, "gid": entry.pw_gid, "groups": groups})
    return results


def probe_os_metrics(storages, *, read_text, statvfs=os.statvfs):
    metrics = {"load1": None, "load5": None, "load15": None,
               "memory_total_kb": None, "memory_available_kb": None,
               "disks": [], "network_rx_bytes": None, "network_tx_bytes": None}
    try:
        parts = read_text("/proc/loadavg").split()
        metrics["load1"], metrics["load5"], metrics["load15"] = (
            float(parts[0]), float(parts[1]), float(parts[2]))
    except Exception:
        pass
    try:
        for line in read_text("/proc/meminfo").splitlines():
            if line.startswith("MemTotal:"):
                metrics["memory_total_kb"] = int(line.split()[1])
            elif line.startswith("MemAvailable:"):
                metrics["memory_available_kb"] = int(line.split()[1])
    except Exception:
        pass
    for storage in storages:
        try:
            vfs = statvfs(storage["mount_path"])
            total = vfs.f_frsize * vfs.f_blocks
            used = vfs.f_frsize * (vfs.f_blocks - vfs.f_bavail)
            metrics["disks"].append({"storage_name": storage["storage_name"],
                                     "total_bytes": total, "used_bytes": used})
        except Exception:
            continue
    try:
        rx = tx = 0
        for line in read_text("/proc/net/dev").splitlines()[2:]:
            name, _, rest = line.partition(":")
            if not rest or name.strip() == "lo":
                continue
            fields = rest.split()
            rx += int(fields[0])
            tx += int(fields[8])
        metrics["network_rx_bytes"], metrics["network_tx_bytes"] = rx, tx
    except Exception:
        pass
    return metrics
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/pytest tests/test_agent_probes.py -v` 후 `.venv/bin/pytest -q`
Expected: 전체 PASS

- [ ] **Step 5: Commit**

```bash
git add src/dms/agent/probes.py tests/test_agent_probes.py
git commit -m "feat: 에이전트 도구/identity/OS 프로브 (항목별 fail-soft)"
```

---

### Task 6: 에이전트 러너 (`agent/runner.py`) + httpx 의존성 승격

**Files:**
- Create: `src/dms/agent/runner.py`
- Modify: `pyproject.toml` (dependencies에 `"httpx>=0.27"` 추가 — test extra에서도 유지 무방)
- Test: `tests/test_agent_runner.py`

**Interfaces:**
- Consumes: `AgentSettings`, `AGENT_TOOL_NAMES`(Task 3), probes(Task 4·5), `utc_now_iso`.
- Produces:
  - `build_report(node_name: str, storages: list[dict], probe_targets: list[str], *, mountinfo_text: str, tool_names=AGENT_TOOL_NAMES, mounts_fn=probe_mounts, tools_fn=probe_tools, identities_fn=probe_identities, os_fn=probe_os_metrics, read_text=_read_text) -> dict` — `{node_name, probed_at, mounts, tools, identities, os}`
  - `_read_text(path) -> str` — 파일 읽기 기본 구현
  - `AgentRunner(settings: AgentSettings, client: httpx.Client)`:
    - `.run_once(state: dict) -> dict` — state = `{"storages": [...], "probe_targets": [...], "interval": int}`. mountinfo를 `settings.mountinfo_path`에서 읽어(read 실패 시 빈 문자열) 리포트 생성 → `POST {api_url}/api/agent/report` (헤더 `Authorization: Bearer <token>`, `x-dms-actor: node:<node_name>`) → 200이면 응답의 `{storages, identity_probe_targets, report_interval_seconds}`로 새 state 반환, 그 외/예외면 기존 state 그대로 반환(fail-soft, stderr에 한 줄 로그)
  - `run_loop(settings: AgentSettings, *, once: bool = False) -> None` — 실제 `httpx.Client`로 state를 유지하며 반복, `time.sleep(state["interval"])`
- 첫 사이클은 storages가 빈 상태로 POST → 응답으로 목록 수신 → 다음 사이클부터 실제 프로브 (스펙 §6 응답 채널)

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/test_agent_runner.py
import httpx
from dms.config import AgentSettings
from dms.agent.runner import AgentRunner, build_report

SETTINGS = AgentSettings(api_url="http://api", shared_token="tok", node_name="node-a",
                         interval_seconds=60, mountinfo_path="/unused")


def test_build_report_shape():
    report = build_report(
        "node-a", [{"storage_name": "s", "mount_path": "/mnt/s"}], ["alice"],
        mountinfo_text="1 1 0:1 / /mnt/s rw - ext4 d rw\n",
        tools_fn=lambda names, **k: [{"name": n, "status": "Ready"} for n in names],
        identities_fn=lambda users, **k: [{"username": u, "status": "Ready"} for u in users],
        os_fn=lambda storages, **k: {"load1": 0.1},
    )
    assert report["node_name"] == "node-a"
    assert report["probed_at"].endswith("Z")
    assert report["mounts"][0]["status"] == "Ready"
    assert report["tools"][0]["name"] == "dscan"
    assert report["identities"] == [{"username": "alice", "status": "Ready"}]
    assert report["os"] == {"load1": 0.1}


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://api")


def test_run_once_posts_and_updates_state(monkeypatch):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["actor"] = request.headers["x-dms-actor"]
        return httpx.Response(200, json={
            "storages": [{"storage_name": "s", "mount_path": "/mnt/s",
                          "managed_root": "/mnt/s/dms"}],
            "identity_probe_targets": ["alice"],
            "report_interval_seconds": 15,
        })

    monkeypatch.setattr("dms.agent.runner._read_text", lambda path: "")
    runner = AgentRunner(SETTINGS, _client(handler))
    state = runner.run_once({"storages": [], "probe_targets": [], "interval": 60})
    assert seen["url"] == "http://api/api/agent/report"
    assert seen["auth"] == "Bearer tok" and seen["actor"] == "node:node-a"
    assert state["storages"][0]["storage_name"] == "s"
    assert state["probe_targets"] == ["alice"]
    assert state["interval"] == 15


def test_run_once_keeps_state_on_error(monkeypatch, capsys):
    def handler(request):
        return httpx.Response(500, text="boom")

    monkeypatch.setattr("dms.agent.runner._read_text", lambda path: "")
    runner = AgentRunner(SETTINGS, _client(handler))
    old = {"storages": [{"storage_name": "keep", "mount_path": "/k"}],
           "probe_targets": ["bob"], "interval": 60}
    assert runner.run_once(old) == old
    assert "agent report failed" in capsys.readouterr().err


def test_run_once_survives_connect_error(monkeypatch, capsys):
    def handler(request):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr("dms.agent.runner._read_text", lambda path: "")
    runner = AgentRunner(SETTINGS, _client(handler))
    old = {"storages": [], "probe_targets": [], "interval": 60}
    assert runner.run_once(old) == old
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_agent_runner.py -v`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: 구현**

pyproject.toml `dependencies`에 `"httpx>=0.27",` 추가 후 `.venv/bin/pip install -q -e ".[test]"`.

```python
# src/dms/agent/runner.py
"""에이전트 러너: 프로브 → POST → 응답으로 설정 갱신. DB를 모르는 순수 HTTP 클라이언트."""
import sys
import time

import httpx

from ..config import AGENT_TOOL_NAMES, AgentSettings
from ..db import utc_now_iso
from .probes import probe_identities, probe_mounts, probe_os_metrics, probe_tools


def _read_text(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def build_report(node_name, storages, probe_targets, *, mountinfo_text,
                 tool_names=AGENT_TOOL_NAMES, mounts_fn=probe_mounts,
                 tools_fn=probe_tools, identities_fn=probe_identities,
                 os_fn=probe_os_metrics, read_text=_read_text) -> dict:
    return {
        "node_name": node_name,
        "probed_at": utc_now_iso(),
        "mounts": mounts_fn(storages, mountinfo_text=mountinfo_text),
        "tools": tools_fn(list(tool_names)),
        "identities": identities_fn(probe_targets),
        "os": os_fn(storages, read_text=read_text),
    }


class AgentRunner:
    def __init__(self, settings: AgentSettings, client: httpx.Client):
        self._settings = settings
        self._client = client

    def run_once(self, state: dict) -> dict:
        try:
            mountinfo_text = _read_text(self._settings.mountinfo_path)
        except OSError:
            mountinfo_text = ""
        report = build_report(self._settings.node_name, state["storages"],
                              state["probe_targets"], mountinfo_text=mountinfo_text)
        try:
            response = self._client.post(
                "/api/agent/report", json=report,
                headers={
                    "Authorization": f"Bearer {self._settings.shared_token}",
                    "x-dms-actor": f"node:{self._settings.node_name}",
                })
            response.raise_for_status()
            body = response.json()
        except Exception as exc:
            print(f"agent report failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return state
        return {
            "storages": body.get("storages", state["storages"]),
            "probe_targets": body.get("identity_probe_targets", state["probe_targets"]),
            "interval": body.get("report_interval_seconds", state["interval"]),
        }


def run_loop(settings: AgentSettings, *, once: bool = False) -> None:
    state = {"storages": [], "probe_targets": [],
             "interval": settings.interval_seconds}
    with httpx.Client(base_url=settings.api_url, timeout=10.0) as client:
        runner = AgentRunner(settings, client)
        while True:
            state = runner.run_once(state)
            if once:
                return
            time.sleep(max(1, int(state["interval"])))
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/pytest tests/test_agent_runner.py -v` 후 `.venv/bin/pytest -q`
Expected: 전체 PASS

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src/dms/agent/runner.py tests/test_agent_runner.py
git commit -m "feat: 에이전트 러너 (POST 리포트, 응답 채널로 설정 수신, fail-soft)"
```

---

### Task 7: 리포트 수집 API (`api/routes_agent.py`)

**Files:**
- Create: `src/dms/api/routes_agent.py`
- Modify: `src/dms/api/app.py` (라우터 마운트)
- Test: `tests/test_api_agent.py`

**Interfaces:**
- Consumes: `require_user`(bearer shared token이면 role=admin, actor=`x-dms-actor`), `repos.agents.ingest`, `repos.storages.list`, `repos.control.probe_targets`, `settings.agent_report_interval_seconds`, `settings.identity_probe_ttl_seconds`.
- Produces:
  - `POST /api/agent/report` — body는 dict(최소 `node_name: str` 필수). 검증:
    - `node_name` 없거나 str 아니거나 공백 포함/빈 문자열 → 422 `invalid_node_name`
    - 인증 identity의 actor가 정확히 `node:{node_name}`이 아니면 → 403 `agent_node_identity_mismatch` (세션 사용자도 이 규칙으로 403)
  - 성공: `repos.agents.ingest(node_name, body)` 후 200 `{storages: [{storage_name, mount_path, managed_root}] (enabled=1만), identity_probe_targets: [...], report_interval_seconds: N}`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/test_api_agent.py
def _agent_headers(node="node-a"):
    return {"Authorization": "Bearer tok-shared", "x-dms-actor": f"node:{node}"}


ADMIN = {"Authorization": "Bearer tok-shared", "x-dms-actor": "ops"}
REPORT = {"node_name": "node-a", "mounts": [], "tools": [], "identities": [], "os": {}}


def test_report_roundtrip_returns_config(client):
    client.post("/api/admin/storages", json={
        "storage_name": "ceph-a", "mount_path": "/mnt/ceph",
        "managed_root": "/mnt/ceph/dms", "backend_type": "cephfs"}, headers=ADMIN)
    r = client.post("/api/agent/report", json=REPORT, headers=_agent_headers())
    assert r.status_code == 200
    body = r.json()
    assert body["storages"] == [{"storage_name": "ceph-a", "mount_path": "/mnt/ceph",
                                 "managed_root": "/mnt/ceph/dms"}]
    assert body["identity_probe_targets"] == []
    assert body["report_interval_seconds"] == 60


def test_disabled_storage_not_served(client):
    client.post("/api/admin/storages", json={
        "storage_name": "ceph-a", "mount_path": "/mnt/ceph",
        "managed_root": "/mnt/ceph/dms", "backend_type": "cephfs"}, headers=ADMIN)
    client.put("/api/admin/storages/ceph-a", json={
        "mount_path": "/mnt/ceph", "managed_root": "/mnt/ceph/dms",
        "backend_type": "cephfs", "enabled": False}, headers=ADMIN)
    r = client.post("/api/agent/report", json=REPORT, headers=_agent_headers())
    assert r.json()["storages"] == []


def test_actor_mismatch_403(client):
    r = client.post("/api/agent/report", json=REPORT,
                    headers=_agent_headers(node="node-b"))
    assert r.status_code == 403
    assert r.json()["detail"] == "agent_node_identity_mismatch"


def test_session_user_cannot_report(client):
    client.post("/api/auth/signup", json={"username": "u1", "password": "p"})
    client.post("/api/auth/login", json={"username": "u1", "password": "p"})
    r = client.post("/api/agent/report", json=REPORT)
    assert r.status_code == 403


def test_invalid_node_name_422(client):
    r = client.post("/api/agent/report", json={"node_name": "bad name"},
                    headers={"Authorization": "Bearer tok-shared",
                             "x-dms-actor": "node:bad name"})
    assert r.status_code == 422 and r.json()["detail"] == "invalid_node_name"


def test_report_is_persisted(client, db):
    client.post("/api/agent/report", json=REPORT, headers=_agent_headers())
    assert db.query_one("SELECT node_name FROM agent_nodes")["node_name"] == "node-a"
    assert len(db.query("SELECT id FROM agent_reports")) == 1
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_api_agent.py -v`
Expected: FAIL (404)

- [ ] **Step 3: 구현**

```python
# src/dms/api/routes_agent.py
from fastapi import APIRouter, Depends, HTTPException, Request

from .auth import Identity, require_user

router = APIRouter()


@router.post("/api/agent/report")
def ingest_report(body: dict, request: Request,
                  identity: Identity = Depends(require_user)):
    node_name = body.get("node_name")
    if (not isinstance(node_name, str) or not node_name
            or any(ch.isspace() for ch in node_name)):
        raise HTTPException(status_code=422, detail="invalid_node_name")
    if identity.actor != f"node:{node_name}":
        raise HTTPException(status_code=403, detail="agent_node_identity_mismatch")
    repos = request.app.state.repos
    settings = request.app.state.settings
    repos.agents.ingest(node_name, body)
    storages = [{"storage_name": s["storage_name"], "mount_path": s["mount_path"],
                 "managed_root": s["managed_root"]}
                for s in repos.storages.list() if s["enabled"]]
    return {
        "storages": storages,
        "identity_probe_targets": repos.control.probe_targets(
            ttl_seconds=settings.identity_probe_ttl_seconds),
        "report_interval_seconds": settings.agent_report_interval_seconds,
    }
```

주의: `test_invalid_node_name_422`는 actor에 공백이 있어도 **node_name 검증이 actor 검증보다 먼저**임을 고정한다 — 위 코드 순서 유지. `app.py`에 `from .routes_agent import router as agent_router` + `app.include_router(agent_router)`.

`tests/test_api_agent.py`의 `client, db` 동시 사용을 위해 conftest의 `client` 픽스처가 같은 `db`를 쓰는지 확인 (Phase 1 conftest가 이미 `client(db, settings)` 구조 — 그대로 동작).

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/pytest tests/test_api_agent.py -v` 후 `.venv/bin/pytest -q`
Expected: 전체 PASS

- [ ] **Step 5: Commit**

```bash
git add src/dms/api/ tests/test_api_agent.py
git commit -m "feat: 에이전트 리포트 수집 API (node actor 검증, 응답으로 설정 전달)"
```

---

### Task 8: 노드 조회 API (`api/routes_nodes.py`)

**Files:**
- Create: `src/dms/api/routes_nodes.py`
- Modify: `src/dms/api/app.py` (라우터 마운트)
- Test: `tests/test_api_nodes.py`

**Interfaces:**
- Consumes: `require_admin`, `repos.agents.list_nodes/node_reports`, `settings.agent_report_stale_seconds`.
- Produces (admin 전용):
  - `GET /api/admin/nodes` → `list_nodes(stale_seconds=settings.agent_report_stale_seconds)` 그대로
  - `GET /api/admin/nodes/{name}/reports?limit=100` (limit 1..1000 bound) → 이력. 이력이 0건이면 404 `node_not_found` (retention이 이력을 전부 지운 장수 무응답 노드도 404 — 목록 API에는 여전히 stale로 보인다)

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/test_api_nodes.py
ADMIN = {"Authorization": "Bearer tok-shared", "x-dms-actor": "ops"}


def _ingest(client, node, seq=0):
    client.post("/api/agent/report",
                json={"node_name": node, "seq": seq},
                headers={"Authorization": "Bearer tok-shared",
                         "x-dms-actor": f"node:{node}"})


def test_nodes_require_admin(client):
    assert client.get("/api/admin/nodes").status_code == 401
    client.post("/api/auth/signup", json={"username": "u1", "password": "p"})
    client.post("/api/auth/login", json={"username": "u1", "password": "p"})
    assert client.get("/api/admin/nodes").status_code == 403


def test_nodes_list_and_history(client):
    _ingest(client, "node-a", seq=1)
    _ingest(client, "node-a", seq=2)
    _ingest(client, "node-b")
    nodes = client.get("/api/admin/nodes", headers=ADMIN).json()
    assert [n["node_name"] for n in nodes] == ["node-a", "node-b"]
    assert all(n["fresh"] for n in nodes)  # 방금 수집 — 신선
    history = client.get("/api/admin/nodes/node-a/reports?limit=1",
                         headers=ADMIN).json()
    assert len(history) == 1 and history[0]["report"]["seq"] == 2


def test_unknown_node_404_and_limit_bound(client):
    r = client.get("/api/admin/nodes/ghost/reports", headers=ADMIN)
    assert r.status_code == 404 and r.json()["detail"] == "node_not_found"
    _ingest(client, "node-a")
    assert client.get("/api/admin/nodes/node-a/reports?limit=0",
                      headers=ADMIN).status_code == 422
    assert client.get("/api/admin/nodes/node-a/reports?limit=5000",
                      headers=ADMIN).status_code == 422
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_api_nodes.py -v`
Expected: FAIL (404)

- [ ] **Step 3: 구현**

```python
# src/dms/api/routes_nodes.py
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from .auth import require_admin

router = APIRouter(dependencies=[Depends(require_admin)])


@router.get("/api/admin/nodes")
def list_nodes(request: Request):
    settings = request.app.state.settings
    return request.app.state.repos.agents.list_nodes(
        stale_seconds=settings.agent_report_stale_seconds)


@router.get("/api/admin/nodes/{name}/reports")
def node_reports(name: str, request: Request,
                 limit: int = Query(default=100, ge=1, le=1000)):
    repos = request.app.state.repos
    rows = repos.agents.node_reports(name, limit=limit)
    if not rows:
        raise HTTPException(status_code=404, detail="node_not_found")
    return rows
```

`app.py`에 `from .routes_nodes import router as nodes_router` + `app.include_router(nodes_router)`.

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/pytest tests/test_api_nodes.py -v` 후 `.venv/bin/pytest -q`
Expected: 전체 PASS

- [ ] **Step 5: Commit**

```bash
git add src/dms/api/ tests/test_api_nodes.py
git commit -m "feat: 노드 조회 API (신선도 포함 목록, 리포트 이력)"
```

---

### Task 9: storage-reconciler (`reconciler.py`)

**Files:**
- Create: `src/dms/reconciler.py`
- Test: `tests/test_reconciler.py`

**Interfaces:**
- Consumes: `Repositories`(`.storages.list/.set_status`, `.agents.fresh_reports`).
- Produces:
  - `reconcile_storages_once(repos, *, stale_seconds: int, now_iso: str | None = None) -> dict[str, str]` — enabled=1인 storage마다 신선한 `agent_nodes` 리포트의 `mounts` 증거를 모아 상태 판정:
    - 증거 0건 → `Unknown` (detail `no_fresh_agent_evidence`)
    - 전부 `status=="Ready"` → `Ready` (detail `ready_nodes=N`)
    - 일부만 Ready → `Degraded` (detail `ready_nodes=K/N`)
    - Ready 0건 → `Degraded` (detail `no_ready_mounts (nodes=N)`)
  - 판정이 기존 `status`와 다를 때만 `set_status` 호출 (updated_at 불필요한 churn 방지). enabled=0은 건드리지 않는다. 반환값은 storage_name → 판정 상태 (변경 여부 무관, 전 enabled storage)

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/test_reconciler.py
from dms.reconciler import reconcile_storages_once
from dms.repositories import Repositories

NOW = "2026-08-02T10:00:00Z"


def _setup(db, mounts_by_node):
    repos = Repositories(db)
    repos.storages.create(storage_name="ceph-a", mount_path="/mnt/ceph",
                          managed_root="/mnt/ceph/dms", backend_type="cephfs",
                          actor="admin")
    for node, mounts in mounts_by_node.items():
        repos.agents.ingest(node, {"node_name": node, "mounts": mounts},
                            reported_at="2026-08-02T09:59:00Z")
    return repos


def _mount(status):
    return {"storage_name": "ceph-a", "mount_path": "/mnt/ceph", "status": status}


def test_no_evidence_is_unknown(db):
    repos = _setup(db, {})
    assert reconcile_storages_once(repos, stale_seconds=300, now_iso=NOW) == {
        "ceph-a": "Unknown"}
    row = repos.storages.get("ceph-a")
    assert row["status"] == "Unknown"
    assert row["status_detail"] == "no_fresh_agent_evidence"


def test_all_ready_is_ready(db):
    repos = _setup(db, {"n1": [_mount("Ready")], "n2": [_mount("Ready")]})
    assert reconcile_storages_once(repos, stale_seconds=300, now_iso=NOW) == {
        "ceph-a": "Ready"}
    assert repos.storages.get("ceph-a")["status_detail"] == "ready_nodes=2"


def test_partial_ready_is_degraded(db):
    repos = _setup(db, {"n1": [_mount("Ready")], "n2": [_mount("Missing")]})
    reconcile_storages_once(repos, stale_seconds=300, now_iso=NOW)
    row = repos.storages.get("ceph-a")
    assert row["status"] == "Degraded" and row["status_detail"] == "ready_nodes=1/2"


def test_stale_evidence_is_ignored(db):
    repos = _setup(db, {"n1": [_mount("Ready")]})
    # 리포트가 10분 전 — stale 300s 기준으로 무시 → Unknown
    result = reconcile_storages_once(repos, stale_seconds=300,
                                     now_iso="2026-08-02T10:09:00Z")
    assert result == {"ceph-a": "Unknown"}


def test_unchanged_status_does_not_touch_row(db):
    repos = _setup(db, {"n1": [_mount("Ready")]})
    reconcile_storages_once(repos, stale_seconds=300, now_iso=NOW)
    before = repos.storages.get("ceph-a")["updated_at"]
    reconcile_storages_once(repos, stale_seconds=300, now_iso=NOW)
    assert repos.storages.get("ceph-a")["updated_at"] == before


def test_disabled_storage_skipped(db):
    repos = _setup(db, {"n1": [_mount("Ready")]})
    repos.storages.update("ceph-a", mount_path="/mnt/ceph",
                          managed_root="/mnt/ceph/dms", backend_type="cephfs",
                          enabled=False, actor="admin")
    assert reconcile_storages_once(repos, stale_seconds=300, now_iso=NOW) == {}
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_reconciler.py -v`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: 구현**

```python
# src/dms/reconciler.py
"""storage-reconciler: 신선한 에이전트 증거만으로 storages.status를 재계산하는 루프 본체."""
from .repositories import Repositories


def reconcile_storages_once(repos: Repositories, *, stale_seconds: int,
                            now_iso: str | None = None) -> dict[str, str]:
    fresh = repos.agents.fresh_reports(stale_seconds=stale_seconds, now_iso=now_iso)
    result: dict[str, str] = {}
    for storage in repos.storages.list():
        if not storage["enabled"]:
            continue
        name = storage["storage_name"]
        statuses = []
        for node in fresh:
            for mount in (node["report"] or {}).get("mounts", []):
                if mount.get("storage_name") == name:
                    statuses.append(mount.get("status"))
        total = len(statuses)
        ready = sum(1 for s in statuses if s == "Ready")
        if total == 0:
            status, detail = "Unknown", "no_fresh_agent_evidence"
        elif ready == total:
            status, detail = "Ready", f"ready_nodes={total}"
        elif ready > 0:
            status, detail = "Degraded", f"ready_nodes={ready}/{total}"
        else:
            status, detail = "Degraded", f"no_ready_mounts (nodes={total})"
        result[name] = status
        if storage["status"] != status or storage["status_detail"] != detail:
            repos.storages.set_status(name, status, detail)
    return result
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/pytest tests/test_reconciler.py -v` 후 `.venv/bin/pytest -q`
Expected: 전체 PASS

- [ ] **Step 5: Commit**

```bash
git add src/dms/reconciler.py tests/test_reconciler.py
git commit -m "feat: storage-reconciler (신선 증거 기반 Ready/Degraded/Unknown 재계산)"
```

---

### Task 10: retention 루프 (`retention.py`)

**Files:**
- Create: `src/dms/retention.py`
- Test: `tests/test_retention.py`

**Interfaces:**
- Consumes: `repos.agents.prune_reports`, `iso_plus`, `utc_now_iso`.
- Produces: `prune_agent_reports_once(repos, *, retention_days: int, now_iso: str | None = None, batch_size: int = 5000) -> int` — cutoff = now - retention_days일. 삭제 수 반환. `agent_nodes` 불변.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/test_retention.py
from dms.repositories import Repositories
from dms.retention import prune_agent_reports_once


def test_prunes_old_history_only(db):
    repos = Repositories(db)
    repos.agents.ingest("n1", {}, reported_at="2026-07-01T00:00:00Z")
    repos.agents.ingest("n1", {}, reported_at="2026-08-01T00:00:00Z")
    deleted = prune_agent_reports_once(repos, retention_days=30,
                                       now_iso="2026-08-02T00:00:00Z")
    assert deleted == 1
    remaining = db.query("SELECT reported_at FROM agent_reports")
    assert remaining == [{"reported_at": "2026-08-01T00:00:00Z"}]
    assert db.query_one("SELECT node_name FROM agent_nodes") == {"node_name": "n1"}


def test_nothing_to_prune_returns_zero(db):
    repos = Repositories(db)
    repos.agents.ingest("n1", {}, reported_at="2026-08-01T00:00:00Z")
    assert prune_agent_reports_once(repos, retention_days=30,
                                    now_iso="2026-08-02T00:00:00Z") == 0
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_retention.py -v`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: 구현**

```python
# src/dms/retention.py
"""retention: agent_reports 이력을 보존 기간 밖에서 배치 삭제. correctness가 아니라 최적화."""
from .db import iso_plus, utc_now_iso
from .repositories import Repositories


def prune_agent_reports_once(repos: Repositories, *, retention_days: int,
                             now_iso: str | None = None,
                             batch_size: int = 5000) -> int:
    now = now_iso or utc_now_iso()
    cutoff = iso_plus(now, -retention_days * 86400)
    return repos.agents.prune_reports(cutoff, batch_size=batch_size)
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/pytest tests/test_retention.py -v` 후 `.venv/bin/pytest -q`
Expected: 전체 PASS

- [ ] **Step 5: Commit**

```bash
git add src/dms/retention.py tests/test_retention.py
git commit -m "feat: agent_reports retention 루프 본체"
```

---

### Task 11: controller 숙주 + CLI 서브커맨드 (`controller.py`, `cli.py`)

**Files:**
- Create: `src/dms/controller.py`
- Modify: `src/dms/cli.py` (`controller`, `agent` 서브커맨드)
- Test: `tests/test_controller.py`, `tests/test_cli.py`에 추가

**Interfaces:**
- Consumes: `Repositories`, `Settings`, `AgentSettings`, `reconcile_storages_once`, `prune_agent_reports_once`, `ControlRepository.try_acquire_lease`, `dms.agent.runner.run_loop`.
- Produces:
  - `@dataclass Loop`: `name: str`, `interval_seconds: int`, `fn: Callable[[], object]`
  - `build_loops(settings: Settings, repos: Repositories) -> list[Loop]` — `storage-reconciler`(reconcile_interval_seconds), `retention`(retention_interval_seconds)
  - `run_all_once(loops, repos, holder: str) -> dict[str, str]` — 루프마다 `try_acquire_lease(f"loop:{name}", holder, lease_seconds=max(interval*3, 30))`: 실패 → `"skipped_lease"`, 예외 → `"error:<TypeName>"`(stderr 로그, 다른 루프 계속), 성공 → `"ok"`
  - `run_forever(settings, repos, holder, *, sleep=time.sleep) -> None` — 루프별 다음 실행 시각을 메모리로 관리(죽으면 처음부터 — 상태는 DB에 없어도 무해), 1초 틱
  - CLI: `dms controller [--once]` — Settings 로딩 → DB 접속 → `run_all_once`(--once) 또는 `run_forever`. holder는 `controller-<pid>`
  - CLI: `dms agent [--once]` — **AgentSettings만** 로딩 (DB/서버 Settings 불필요) → `run_loop(agent_settings, once=args.once)`. 설정 문제 시 기존과 동일하게 exit 2 + stderr

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/test_controller.py
from dms.controller import Loop, build_loops, run_all_once
from dms.repositories import Repositories


def test_build_loops_names_and_intervals(db, settings):
    loops = build_loops(settings, Repositories(db))
    assert [(l.name, l.interval_seconds) for l in loops] == [
        ("storage-reconciler", settings.reconcile_interval_seconds),
        ("retention", settings.retention_interval_seconds)]


def test_run_all_once_runs_and_isolates_errors(db, capsys):
    repos = Repositories(db)
    calls = []

    def ok():
        calls.append("ok")

    def boom():
        raise RuntimeError("loop crashed")

    loops = [Loop("good", 30, ok), Loop("bad", 30, boom)]
    result = run_all_once(loops, repos, holder="h1")
    assert result == {"good": "ok", "bad": "error:RuntimeError"}
    assert calls == ["ok"]
    assert "loop crashed" in capsys.readouterr().err


def test_run_all_once_respects_leases(db):
    repos = Repositories(db)
    loops = [Loop("solo", 30, lambda: None)]
    assert run_all_once(loops, repos, holder="h1") == {"solo": "ok"}
    # 다른 holder는 리스 만료 전 skip
    assert run_all_once(loops, repos, holder="h2") == {"solo": "skipped_lease"}
    # 같은 holder는 갱신되어 계속 실행
    assert run_all_once(loops, repos, holder="h1") == {"solo": "ok"}


def test_reconciler_loop_wired_end_to_end(db, settings):
    repos = Repositories(db)
    repos.storages.create(storage_name="s1", mount_path="/mnt/s",
                          managed_root="/mnt/s/dms", backend_type="cephfs",
                          actor="admin")
    loops = build_loops(settings, repos)
    run_all_once(loops, repos, holder="h1")
    assert repos.storages.get("s1")["status"] == "Unknown"
```

`tests/test_cli.py`에 추가:

```python
def test_controller_once(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DMS_DATABASE_URL", f"sqlite:///{tmp_path}/c.db")
    monkeypatch.setenv("DMS_SHARED_TOKEN", "t")
    monkeypatch.setenv("DMS_ADMIN_TOKEN", "a")
    monkeypatch.setenv("DMS_SESSION_SECRET", "s")
    assert main(["migrate"]) == 0
    assert main(["controller", "--once"]) == 0
    out = capsys.readouterr().out
    assert "storage-reconciler=ok" in out and "retention=ok" in out


def test_agent_once_uses_agent_settings(monkeypatch):
    called = {}

    def fake_run_loop(settings, *, once):
        called["node"] = settings.node_name
        called["once"] = once

    monkeypatch.setenv("DMS_AGENT_API_URL", "http://api")
    monkeypatch.setenv("DMS_AGENT_NODE_NAME", "node-x")
    monkeypatch.setenv("DMS_SHARED_TOKEN", "t")
    monkeypatch.delenv("DMS_DATABASE_URL", raising=False)  # 서버 설정 없이도 동작해야 함
    monkeypatch.setattr("dms.agent.runner.run_loop", fake_run_loop)
    from dms.cli import main as cli_main
    assert cli_main(["agent", "--once"]) == 0
    assert called == {"node": "node-x", "once": True}


def test_agent_fails_closed_on_bad_settings(monkeypatch, capsys):
    monkeypatch.delenv("DMS_AGENT_API_URL", raising=False)
    monkeypatch.setenv("DMS_SHARED_TOKEN", "t")
    from dms.cli import main as cli_main
    assert cli_main(["agent", "--once"]) == 2
    assert "DMS_AGENT_API_URL" in capsys.readouterr().err
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_controller.py tests/test_cli.py -v`
Expected: 새 테스트 FAIL

- [ ] **Step 3: 구현**

```python
# src/dms/controller.py
"""controller 숙주: 재시작 가능한 run_once 루프들을 리더 리스 아래에서 반복 실행."""
import sys
import time
from dataclasses import dataclass
from typing import Callable

from .config import Settings
from .reconciler import reconcile_storages_once
from .repositories import Repositories
from .retention import prune_agent_reports_once


@dataclass
class Loop:
    name: str
    interval_seconds: int
    fn: Callable[[], object]


def build_loops(settings: Settings, repos: Repositories) -> list[Loop]:
    return [
        Loop("storage-reconciler", settings.reconcile_interval_seconds,
             lambda: reconcile_storages_once(
                 repos, stale_seconds=settings.agent_report_stale_seconds)),
        Loop("retention", settings.retention_interval_seconds,
             lambda: prune_agent_reports_once(
                 repos, retention_days=settings.agent_report_retention_days)),
    ]


def run_all_once(loops: list[Loop], repos: Repositories, holder: str) -> dict[str, str]:
    results: dict[str, str] = {}
    for loop in loops:
        acquired = repos.control.try_acquire_lease(
            f"loop:{loop.name}", holder,
            lease_seconds=max(loop.interval_seconds * 3, 30))
        if not acquired:
            results[loop.name] = "skipped_lease"
            continue
        try:
            loop.fn()
            results[loop.name] = "ok"
        except Exception as exc:  # 한 루프의 실패가 다른 루프를 죽이지 않는다
            print(f"loop {loop.name} failed: {type(exc).__name__}: {exc}",
                  file=sys.stderr)
            results[loop.name] = f"error:{type(exc).__name__}"
    return results


def run_forever(settings: Settings, repos: Repositories, holder: str,
                *, sleep=time.sleep) -> None:
    loops = build_loops(settings, repos)
    next_due = {loop.name: 0.0 for loop in loops}
    while True:
        now = time.monotonic()
        for loop in loops:
            if now < next_due[loop.name]:
                continue
            run_all_once([loop], repos, holder)
            next_due[loop.name] = now + loop.interval_seconds
        sleep(1)
```

`src/dms/cli.py` 수정 — 서브커맨드 추가와 분기 (agent는 서버 Settings를 로딩하지 않도록 **명령 분기를 Settings 로딩보다 앞으로** 재구성):

```python
import argparse
import os
import sys

from .config import AgentSettings, Settings, SettingsError


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="dms")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="apply database schema")
    sub.add_parser("api", help="run the API server")
    controller = sub.add_parser("controller", help="run controller loops")
    controller.add_argument("--once", action="store_true")
    agent = sub.add_parser("agent", help="run the node agent")
    agent.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)

    if args.command == "agent":
        try:
            agent_settings = AgentSettings.from_env(os.environ)
        except SettingsError as e:
            for problem in e.problems:
                print(f"settings error: {problem}", file=sys.stderr)
            return 2
        from .agent import runner
        runner.run_loop(agent_settings, once=args.once)
        return 0

    try:
        settings = Settings.from_env(os.environ)
    except SettingsError as e:
        for problem in e.problems:
            print(f"settings error: {problem}", file=sys.stderr)
        return 2

    from .db import Database
    db = Database.connect(settings.database_url)

    if args.command == "migrate":
        from .migrations import migrate
        migrate(db)
        print("migrated")
        return 0

    if args.command == "api":
        import uvicorn
        from .api.app import create_app
        uvicorn.run(create_app(settings, db), host=settings.api_host,
                    port=settings.api_port)
        return 0

    if args.command == "controller":
        from .controller import build_loops, run_all_once, run_forever
        from .repositories import Repositories
        repos = Repositories(db)
        holder = f"controller-{os.getpid()}"
        if args.once:
            results = run_all_once(build_loops(settings, repos), repos, holder)
            print(" ".join(f"{k}={v}" for k, v in results.items()))
            return 0
        run_forever(settings, repos, holder)
        return 0

    return 2
```

주의: `test_agent_once_uses_agent_settings`는 `runner.run_loop`를 모듈 속성으로 monkeypatch하므로, agent 분기에서 `from .agent import runner` 후 `runner.run_loop(...)` 형태(모듈 경유 호출)를 유지해야 한다 — `from .agent.runner import run_loop`로 직접 바인딩하면 monkeypatch가 안 먹는다.

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/pytest tests/test_controller.py tests/test_cli.py -v` 후 `.venv/bin/pytest -q`
Expected: 전체 PASS

- [ ] **Step 5: Commit**

```bash
git add src/dms/controller.py src/dms/cli.py tests/test_controller.py tests/test_cli.py
git commit -m "feat: controller 숙주 (리더 리스 루프) + dms controller/agent CLI"
```

---

## Phase 2 완료 기준

- `pytest -q` 전체 통과 (서비스 없이, 0 warnings).
- 수동 검증 시나리오: `dms migrate` → `dms api` 기동 → 스토리지 등록 → 다른 셸에서 `DMS_AGENT_API_URL=... DMS_AGENT_NODE_NAME=n1 dms agent --once` 2회 실행(1회차는 storages 빈 리포트, 2회차부터 마운트 프로브 포함) → `GET /api/admin/nodes`에 노드가 fresh로 보임 → `dms controller --once` 실행 → 스토리지 status가 증거 기반으로 갱신됨.
- Phase 3 플랜 작성으로 이어진다 (planner + job-stepper + identity(LDAP) + Volcano + dms-job-runner).
