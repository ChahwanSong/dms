# 슬라이스 11 — 포탈 주도 이미지 빌드 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 운영자가 포탈에서 git ref와 이미지를 골라 빌드를 걸면, 지정한 노드에서 buildah 파드가 돌아 레지스트리에 새 태그를 올리고, 그 진행·결과·로그를 포탈에서 볼 수 있게 한다.

**Architecture:** 빌드는 **bare Pod**로 돈다 (batch/v1 Job 아님) — 기존 `K8sClient`가 `kind == "Pod"`를 이미 처리하고, `read_pod_log`가 bare Pod ref만 지원하며, 컨트롤러 SA가 이미 pods create/delete를 갖고 있어 RBAC 변경이 0이기 때문. 파드 명세는 순수 함수가 만들고, k8s I/O는 주입된 `K8sClient` 뒤에 두며, `BuildWatcher` 컨트롤러 루프가 상태를 DB로 옮긴다.

**Tech Stack:** Python 3.11 / FastAPI / SQLite+PostgreSQL 양립 SQL, React 18 + Vite + TS + Tailwind + TanStack Query v5 + Vitest/Testing Library/MSW 2, Kubernetes(bare Pod) + buildah.

## Global Constraints

- 설계 문서: `docs/superpowers/specs/2026-08-06-dms-portal-image-build-slice11-design.md`. 충돌하면 설계가 이긴다.
- `legacy/` 아래는 읽기 전용 — 수정·이동·삭제·추가 금지, import 금지.
- 롤아웃(컴포넌트 이미지 교체, `releases` 테이블, `apps/*` RBAC)은 **범위 밖** — 슬라이스 12.
- SQL은 `:named` 파라미터만. `?`·`%s` 금지. dialect 분기는 꼭 필요한 곳만 (`auto_pk`, `FOR UPDATE SKIP LOCKED`).
- 기존 테이블에 컬럼을 더할 땐 **`CREATE TABLE` 텍스트와 `_ensure_columns` 양쪽**에 넣는다 — `CREATE TABLE IF NOT EXISTS`는 기존 테이블을 조용히 건너뛴다.
- 저장소 클래스: 동기, `__init__(self, db)`만, `self._db`에 보관, 뮤테이터 인자는 키워드 전용이며 마지막이 `actor`, `Repositories`에 등록.
- 모든 admin 뮤테이션은 **변경과 같은 트랜잭션 안에서** `audit_log` 행을 쓴다 (`ControlRepository._audit` 패턴). `mutation_class`는 `"build"`.
- 컨트롤러 루프는 인자 없는 콜러블 + 멱등 `run_once()`. `build_loops()`에 등록. 루프 안 예외는 상위에서 삼켜져 stderr로만 나가므로 **실패는 예외가 아니라 DB 상태로 드러낸다.**
- 어댑터 경계를 넘는 실패는 전부 `ExecutionError(reason_code, detail[:200])`.
- k8s 객체 이름은 DNS-1123: 밑줄 금지, 63자 절단.
- 컨테이너 셸 스크립트에 값을 f-string으로 보간하지 않는다 — `DMS_BUILD_*` 환경변수로 넘겨 셸에서 참조.
- 새 백엔드 사유 코드는 전부 `frontend/src/lib/api.ts`의 `REASON_MESSAGES`에 넣는다. 컴포넌트에 한글 에러 문자열 하드코딩 금지.
- admin 화면은 `<RequireRole role="admin"><AppShell>…</AppShell></RequireRole>`로 감싸고 사이드바 링크는 `{isAdmin && …}`.
- 프론트 h1 문자열은 계약이다 — 사이드바 라벨·라우터 테스트·h1 모두 정확히 **빌드**.
- 프론트 UI 문자열은 한국어. 로딩은 `불러오는 중…`, 버튼은 `저장`/`취소`/`상세`, null은 `—`.
- 백엔드 응답은 렌더 전에 방어적으로 정규화한다 (`asArray` 류) — 느슨한 페이로드가 SPA를 흰 화면으로 만들면 안 된다.
- 프론트 테스트는 파일마다 자체 MSW `setupServer` + `listen/resetHandlers/close`. 핸들러 경로는 상대경로(`/api/admin/...`).
- 백엔드 테스트는 `.venv/bin/python -m pytest`로 돌린다. 프론트는 `frontend/`에서 `npx vitest run`, 타입체크는 `npx tsc -b`.
- push 금지 (origin으로 절대 push하지 않는다). 커밋만 한다.

---

## 파일 구조

| 파일 | 책임 |
|---|---|
| `src/dms/migrations.py` (수정) | `control_state.build_node_name`, `builds.log_text` 컬럼 추가 (CREATE TABLE + `_ensure_columns`) |
| `src/dms/repositories/builds.py` (신규) | `builds` 행의 생성·조회·상태 전이. SQL은 전부 여기 |
| `src/dms/repositories/control.py` (수정) | `set_control_state`가 `build_node_name`을 함께 쓴다 |
| `src/dms/repositories/__init__.py` (수정) | `self.builds` 등록 |
| `src/dms/build_manifests.py` (신규) | 순수 함수 `build_build_pod(...)` — 파드 dict만 만든다. k8s 접근 없음 |
| `src/dms/build_runner.py` (신규) | `BuildRunner` — 주입된 `K8sClient`로 파드 생성/폴링/로그/삭제. ref 접두 `buildpod/` |
| `src/dms/build_watcher.py` (신규) | `BuildWatcher.run_once()` — Pending→Running→종단 |
| `src/dms/controller.py` (수정) | `build-watcher` 루프 등록 |
| `src/dms/config.py` (수정) | `build_watcher_interval_seconds`, `build_repo_url` |
| `src/dms/pod_gc.py` (수정) | 종단 빌드의 파드도 수거 |
| `src/dms/wiring.py` (수정) | `build_build_runner(settings)` |
| `src/dms/api/routes_builds.py` (신규) | admin 빌드 API 4종 |
| `src/dms/api/routes_control.py` (수정) | `build_node_name` 필드 |
| `src/dms/api/app.py` (수정) | 라우터 등록, `app.state.build_runner` |
| `frontend/src/features/builds/*` (신규) | 「빌드」 화면 + 훅 + 테스트 |
| `frontend/src/app/router.tsx`, `AppShell.tsx` (수정) | 라우트·내비 |
| `frontend/src/lib/types.ts`, `lib/api.ts` (수정) | 타입, 사유 코드 |
| `deploy/README.md` (수정) | 포탈 빌드 절차와 "GitHub에 push된 커밋만 빌드된다" 제약 |

---

### Task 1: 스키마 + BuildsRepository

**Files:**
- Modify: `src/dms/migrations.py`
- Create: `src/dms/repositories/builds.py`
- Modify: `src/dms/repositories/control.py`
- Modify: `src/dms/repositories/__init__.py`
- Test: `tests/test_builds_repo.py`

**Interfaces:**
- Consumes: `Database` (`query`, `query_one`, `execute`, `transaction`), `utc_now_iso`, `dump_json`/`load_json` from `..db`.
- Produces:
  - `BuildsRepository.create(*, repo_url, git_ref, images, node_name, actor) -> str` (build_id 반환, 상태 `Pending`)
  - `BuildsRepository.get(build_id) -> dict | None`
  - `BuildsRepository.list(limit=50) -> list[dict]`
  - `BuildsRepository.active() -> dict | None` (state ∈ Pending/Running 인 것 하나)
  - `BuildsRepository.pending() -> list[dict]`
  - `BuildsRepository.running() -> list[dict]`
  - `BuildsRepository.mark_running(build_id) -> None`
  - `BuildsRepository.finish(build_id, *, state, reason_code=None, commit_sha=None, log_text=None) -> None`
  - `BuildsRepository.terminal_older_than(seconds, *, limit=200, now_iso=None) -> list[dict]`
  - `ControlRepository.set_control_state(..., build_node_name=...)`
  - 상수 `BUILD_IMAGES = ("dms-mpifileutils", "dms", "dms-agent")` — **의존 순서**
  - `build_tag(build_id) -> str` = `"b" + build_id[:8]`
  - `build_pod_name(build_id) -> str` = `"dms-build-" + build_id[:12]`

`images`·`log_text`는 DB에 문자열로 저장한다: `images`는 `dump_json(list)`, 읽을 때 `load_json`. `get`/`list`/`pending`/`running`/`terminal_older_than`이 돌려주는 dict의 `images`는 항상 **리스트**여야 한다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_builds_repo.py`:

```python
import pytest
from dms.db import Database
from dms.migrations import migrate
from dms.repositories import Repositories
from dms.repositories.builds import BUILD_IMAGES, build_pod_name, build_tag


@pytest.fixture
def repos(tmp_path):
    db = Database(f"sqlite:///{tmp_path}/t.db")
    migrate(db)
    return Repositories(db)


def test_create_returns_pending_build_with_list_images(repos):
    bid = repos.builds.create(repo_url="https://example/r.git", git_ref="main",
                              images=["dms"], node_name="dms-w1", actor="admin")
    row = repos.builds.get(bid)
    assert row["state"] == "Pending"
    assert row["images"] == ["dms"]          # 리스트로 되돌아온다 (JSON 문자열 아님)
    assert row["node_name"] == "dms-w1"
    assert row["commit_sha"] is None


def test_create_writes_audit_row(repos):
    repos.builds.create(repo_url="https://example/r.git", git_ref="main",
                        images=["dms"], node_name="dms-w1", actor="ops")
    entries = repos.control.audit_entries(limit=5)
    assert any(e["mutation_class"] == "build" and e["actor"] == "ops" for e in entries)


def test_active_sees_pending_and_running_only(repos):
    bid = repos.builds.create(repo_url="u", git_ref="main", images=["dms"],
                              node_name="dms-w1", actor="a")
    assert repos.builds.active()["build_id"] == bid
    repos.builds.mark_running(bid)
    assert repos.builds.active()["build_id"] == bid
    repos.builds.finish(bid, state="Succeeded")
    assert repos.builds.active() is None


def test_finish_records_reason_commit_and_log(repos):
    bid = repos.builds.create(repo_url="u", git_ref="main", images=["dms"],
                              node_name="dms-w1", actor="a")
    repos.builds.finish(bid, state="Failed", reason_code="build_failed",
                        commit_sha="abc123", log_text="boom")
    row = repos.builds.get(bid)
    assert (row["state"], row["reason_code"]) == ("Failed", "build_failed")
    assert row["commit_sha"] == "abc123" and row["log_text"] == "boom"
    assert row["finished_at"] is not None


def test_list_is_newest_first(repos):
    a = repos.builds.create(repo_url="u", git_ref="main", images=["dms"],
                            node_name="dms-w1", actor="x")
    repos.builds.finish(a, state="Succeeded")
    b = repos.builds.create(repo_url="u", git_ref="dev", images=["dms"],
                            node_name="dms-w1", actor="x")
    assert [r["build_id"] for r in repos.builds.list(limit=10)][:2] == [b, a]


def test_terminal_older_than_excludes_running(repos):
    a = repos.builds.create(repo_url="u", git_ref="main", images=["dms"],
                            node_name="dms-w1", actor="x")
    repos.builds.mark_running(a)
    assert repos.builds.terminal_older_than(0, now_iso="2999-01-01T00:00:00Z") == []
    repos.builds.finish(a, state="Succeeded")
    got = repos.builds.terminal_older_than(0, now_iso="2999-01-01T00:00:00Z")
    assert [r["build_id"] for r in got] == [a]


def test_control_state_carries_build_node(repos):
    repos.control.set_control_state(maintenance=False, drain=False, reason=None,
                                    build_node_name="dms-w1", actor="ops")
    assert repos.control.control_state()["build_node_name"] == "dms-w1"


def test_derived_names_are_deterministic_and_dns1123():
    bid = "0123456789abcdef0123456789abcdef"
    assert build_tag(bid) == "b01234567"
    name = build_pod_name(bid)
    assert name == "dms-build-0123456789ab"
    assert "_" not in name and len(name) <= 63


def test_build_images_are_in_dependency_order():
    assert BUILD_IMAGES == ("dms-mpifileutils", "dms", "dms-agent")
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_builds_repo.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'dms.repositories.builds'`

- [ ] **Step 3: 마이그레이션에 컬럼을 더한다**

`src/dms/migrations.py`의 `control_state` CREATE TABLE 텍스트에 `build_node_name TEXT` 를, `builds` CREATE TABLE 텍스트에 `log_text TEXT` 를 각각 추가하고, `_ensure_columns`의 튜플에 두 줄을 더한다:

```python
    for table, column, coltype in (
        ("data_jobs", "worker_pool", "TEXT"),
        ("data_jobs", "precondition", "TEXT"),
        ("data_jobs", "confirmed_fingerprint", "TEXT"),
        ("data_jobs", "phase_refs", "TEXT"),
        ("requests", "batch_id", "TEXT"),
        ("control_state", "build_node_name", "TEXT"),
        ("builds", "log_text", "TEXT"),
    ):
```

- [ ] **Step 4: BuildsRepository를 만든다**

`src/dms/repositories/builds.py`:

```python
import uuid

from ..db import Database, dump_json, iso_plus, load_json, utc_now_iso

# 의존 순서다 — dms-agent 가 앞의 둘을 FROM 한다. 이 순서로 빌드하지 않으면 실패한다.
BUILD_IMAGES = ("dms-mpifileutils", "dms", "dms-agent")

_TERMINAL = ("Succeeded", "Failed")
_ACTIVE = ("Pending", "Running")
LOG_TEXT_MAX = 64 * 1024


def build_tag(build_id: str) -> str:
    """빌드마다 유일한 태그. 매니페스트가 전부 imagePullPolicy: IfNotPresent 라
    같은 태그를 다시 push 하면 클러스터가 영영 집어오지 않는다 -- 그래서 커밋 SHA 가
    아니라 빌드 고유 id 에서 뽑는다(같은 커밋을 두 번 빌드하는 건 정상 행위다)."""
    return "b" + build_id[:8]


def build_pod_name(build_id: str) -> str:
    return f"dms-build-{build_id[:12]}"[:63]


def _row(row):
    if row is None:
        return None
    out = dict(row)
    out["images"] = load_json(out.get("images")) or []
    return out


class BuildsRepository:
    def __init__(self, db: Database):
        self._db = db

    def _audit(self, operation, target, before, after, actor):
        self._db.execute(
            """INSERT INTO audit_log (mutation_class, operation, target_key, actor,
                   before_state, after_state, at)
               VALUES ('build', :op, :key, :actor, :b, :a, :at)""",
            {"op": operation, "key": target, "actor": actor,
             "b": dump_json(before) if before is not None else None,
             "a": dump_json(after) if after is not None else None,
             "at": utc_now_iso()})

    def create(self, *, repo_url, git_ref, images, node_name, actor) -> str:
        build_id = uuid.uuid4().hex
        now = utc_now_iso()
        after = {"build_id": build_id, "git_ref": git_ref, "images": list(images),
                 "node_name": node_name}
        with self._db.transaction():
            self._db.execute(
                """INSERT INTO builds (build_id, repo_url, git_ref, images, node_name,
                       state, created_at)
                   VALUES (:id, :url, :ref, :imgs, :node, 'Pending', :now)""",
                {"id": build_id, "url": repo_url, "ref": git_ref,
                 "imgs": dump_json(list(images)), "node": node_name, "now": now})
            self._audit("create", build_id, None, after, actor)
        return build_id

    def get(self, build_id):
        return _row(self._db.query_one(
            "SELECT * FROM builds WHERE build_id = :id", {"id": build_id}))

    def list(self, limit: int = 50):
        rows = self._db.query(
            "SELECT * FROM builds ORDER BY created_at DESC, build_id DESC LIMIT :n",
            {"n": limit})
        return [_row(r) for r in rows]

    def _by_states(self, states, limit=50):
        # IN 절을 :named 로 만들려면 파라미터를 하나씩 풀어야 한다.
        keys = {f"s{i}": s for i, s in enumerate(states)}
        placeholders = ", ".join(f":{k}" for k in keys)
        rows = self._db.query(
            f"""SELECT * FROM builds WHERE state IN ({placeholders})
                ORDER BY created_at ASC, build_id ASC LIMIT :n""",
            {**keys, "n": limit})
        return [_row(r) for r in rows]

    def active(self):
        rows = self._by_states(_ACTIVE, limit=1)
        return rows[0] if rows else None

    def pending(self):
        return self._by_states(("Pending",))

    def running(self):
        return self._by_states(("Running",))

    def mark_running(self, build_id) -> None:
        self._db.execute(
            "UPDATE builds SET state = 'Running' WHERE build_id = :id AND state = 'Pending'",
            {"id": build_id})

    def finish(self, build_id, *, state, reason_code=None, commit_sha=None,
               log_text=None) -> None:
        if log_text is not None and len(log_text) > LOG_TEXT_MAX:
            log_text = log_text[-LOG_TEXT_MAX:]
        self._db.execute(
            """UPDATE builds SET state = :st, reason_code = :rc,
                   commit_sha = COALESCE(:sha, commit_sha),
                   log_text = COALESCE(:log, log_text),
                   finished_at = :now
               WHERE build_id = :id AND state NOT IN ('Succeeded', 'Failed')""",
            {"st": state, "rc": reason_code, "sha": commit_sha, "log": log_text,
             "now": utc_now_iso(), "id": build_id})

    def terminal_older_than(self, seconds: int, *, limit: int = 200, now_iso=None):
        now = now_iso or utc_now_iso()
        cutoff = iso_plus(now, -seconds)
        rows = self._db.query(
            """SELECT * FROM builds
               WHERE state IN ('Succeeded', 'Failed') AND finished_at IS NOT NULL
                 AND finished_at < :cutoff
               ORDER BY finished_at ASC, build_id ASC LIMIT :n""",
            {"cutoff": cutoff, "n": limit})
        return [_row(r) for r in rows]
```

`load_json`이 `..db`에 없으면 그 파일에서 실제 이름을 찾아 쓴다 (`dump_json`의 짝).

- [ ] **Step 5: control_state에 build_node_name을 흘린다**

`src/dms/repositories/control.py`의 `set_control_state`를 바꾼다 — 기존 호출자가 깨지지 않도록 새 인자는 기본값을 준다:

```python
    def set_control_state(self, *, maintenance, drain, reason, actor,
                          build_node_name=None):
        before = self.control_state()
        with self._db.transaction():
            self._db.execute(
                """UPDATE control_state SET maintenance = :m, drain = :d, reason = :r,
                       build_node_name = :bn,
                       changed_by = :actor, changed_at = :now WHERE id = 1""",
                {"m": 1 if maintenance else 0, "d": 1 if drain else 0,
                 "r": reason, "bn": build_node_name, "actor": actor,
                 "now": utc_now_iso()})
            self._audit("control_state", "set", "control_state", before,
                        self.control_state(), actor)
```

- [ ] **Step 6: Repositories에 등록한다**

`src/dms/repositories/__init__.py`에 `from .builds import BuildsRepository` 와 `self.builds = BuildsRepository(db)` 를 더한다.

- [ ] **Step 7: 테스트를 통과시킨다**

Run: `.venv/bin/python -m pytest tests/test_builds_repo.py -q`
Expected: PASS (9 tests)

- [ ] **Step 8: 전체 스위트로 회귀를 확인한다**

Run: `.venv/bin/python -m pytest -q`
Expected: 기존 테스트 전부 PASS. `set_control_state` 시그니처 변경으로 깨지는 곳이 있으면 그 호출부를 고친다.

- [ ] **Step 9: 커밋**

```bash
git add src/dms/migrations.py src/dms/repositories/builds.py src/dms/repositories/control.py src/dms/repositories/__init__.py tests/test_builds_repo.py
git commit -m "feat(builds): builds 저장소와 빌드 노드 지정 컬럼"
```

---

### Task 2: 빌드 파드 매니페스트 (순수 함수)

**Files:**
- Create: `src/dms/build_manifests.py`
- Test: `tests/test_build_manifests.py`

**Interfaces:**
- Consumes: `build_pod_name`, `build_tag`, `BUILD_IMAGES` from `.repositories.builds`.
- Produces: `build_build_pod(*, build_id, repo_url, git_ref, images, node, namespace, registry, builder_image) -> dict`

규칙:
- 값은 **환경변수로만** 넘긴다. 셸 스크립트에 f-string 보간 금지.
- `images`는 `BUILD_IMAGES` 순서로 정렬해 넘긴다 (호출자가 순서를 틀려도 의존 순서가 지켜지게).
- 이름 `dms-build-<build_id[:12]>`, 라벨 `dms.io/build-id`, `dms.io/phase=build`.
- `restartPolicy: Never`, `securityContext.privileged: true`, `nodeSelector: {kubernetes.io/hostname: node}`.
- `/var/lib/containers`에 `emptyDir` — 컨테이너 쓰기 계층 위에서 overlay를 쌓지 않기 위해서다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_build_manifests.py`:

```python
from dms.build_manifests import build_build_pod

BID = "0123456789abcdef0123456789abcdef"


def _pod(images=("dms",)):
    return build_build_pod(build_id=BID, repo_url="https://example/r.git",
                           git_ref="main", images=list(images), node="dms-w1",
                           namespace="dms", registry="pkg-01:5000",
                           builder_image="quay.io/buildah/stable:latest")


def test_pod_identity_and_placement():
    pod = _pod()
    assert pod["kind"] == "Pod" and pod["apiVersion"] == "v1"
    assert pod["metadata"]["name"] == "dms-build-0123456789ab"
    assert pod["metadata"]["namespace"] == "dms"
    assert pod["metadata"]["labels"]["dms.io/build-id"] == BID
    assert pod["metadata"]["labels"]["dms.io/phase"] == "build"
    assert pod["spec"]["nodeSelector"] == {"kubernetes.io/hostname": "dms-w1"}
    assert pod["spec"]["restartPolicy"] == "Never"


def test_container_is_privileged_builder_with_container_storage_volume():
    c = _pod()["spec"]["containers"][0]
    assert c["image"] == "quay.io/buildah/stable:latest"
    assert c["securityContext"]["privileged"] is True
    assert any(m["mountPath"] == "/var/lib/containers" for m in c["volumeMounts"])


def test_values_travel_as_env_not_interpolated_into_the_script():
    c = _pod()["spec"]["containers"][0]
    env = {e["name"]: e["value"] for e in c["env"]}
    assert env["DMS_BUILD_REPO"] == "https://example/r.git"
    assert env["DMS_BUILD_REF"] == "main"
    assert env["DMS_BUILD_TAG"] == "b01234567"
    assert env["DMS_BUILD_REGISTRY"] == "pkg-01:5000"
    script = c["command"][2]
    # 값이 스크립트 본문에 박혀 있으면 주입 표면이 된다
    assert "https://example/r.git" not in script
    assert "b01234567" not in script


def test_images_are_forced_into_dependency_order():
    c = _pod(images=["dms-agent", "dms"])["spec"]["containers"][0]
    env = {e["name"]: e["value"] for e in c["env"]}
    assert env["DMS_BUILD_IMAGES"] == "dms dms-agent"


def test_script_pushes_insecurely_and_emits_the_commit_marker():
    script = _pod()["spec"]["containers"][0]["command"][2]
    assert "--tls-verify=false" in script     # 레지스트리가 평문 HTTP 다
    assert "DMS_COMMIT_SHA=" in script        # 감시 루프가 찾는 마커
    assert "set -eu" in script                # 중간 실패가 성공으로 보이면 안 된다
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_build_manifests.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'dms.build_manifests'`

- [ ] **Step 3: 구현한다**

`src/dms/build_manifests.py`:

```python
"""빌드 파드 매니페스트. 순수 함수 -- k8s 클라이언트에 접근하지 않는다."""
from .repositories.builds import BUILD_IMAGES, build_pod_name, build_tag

# 레지스트리가 평문 HTTP 라 pull 도 insecure 로 열어야 한다: dms-agent 이미지가
# `FROM pkg-01:5000/...` 를 하기 때문이다. push 만 --tls-verify=false 로는 부족하다.
_SCRIPT = r"""
set -eu
mkdir -p /etc/containers/registries.conf.d
printf '[[registry]]\nlocation = "%s"\ninsecure = true\n' "$DMS_BUILD_REGISTRY" \
  > /etc/containers/registries.conf.d/dms-insecure.conf

git clone --depth 1 --branch "$DMS_BUILD_REF" "$DMS_BUILD_REPO" /src
cd /src
echo "DMS_COMMIT_SHA=$(git rev-parse HEAD)"

for img in $DMS_BUILD_IMAGES; do
  case "$img" in
    dms-mpifileutils) dockerfile=deploy/docker/Dockerfile.mpifileutils ;;
    dms)              dockerfile=deploy/docker/Dockerfile.dms ;;
    dms-agent)        dockerfile=deploy/docker/Dockerfile.agent ;;
    *) echo "DMS_BUILD_REASON=unknown_image:$img"; exit 1 ;;
  esac
  ref="$DMS_BUILD_REGISTRY/$img:$DMS_BUILD_TAG"
  echo "=== building $ref ==="
  buildah bud -f "$dockerfile" -t "$ref" .
  buildah push --tls-verify=false "$ref"
  echo "=== pushed $ref ==="
done
echo DMS_BUILD_OK
"""


def build_build_pod(*, build_id, repo_url, git_ref, images, node, namespace,
                    registry, builder_image) -> dict:
    ordered = [i for i in BUILD_IMAGES if i in set(images)]
    env = {
        "DMS_BUILD_REPO": repo_url,
        "DMS_BUILD_REF": git_ref,
        "DMS_BUILD_TAG": build_tag(build_id),
        "DMS_BUILD_REGISTRY": registry,
        "DMS_BUILD_IMAGES": " ".join(ordered),
    }
    return {
        "apiVersion": "v1", "kind": "Pod",
        "metadata": {"name": build_pod_name(build_id), "namespace": namespace,
                     "labels": {"dms.io/build-id": build_id,
                                "dms.io/phase": "build"}},
        "spec": {
            "restartPolicy": "Never",
            "nodeSelector": {"kubernetes.io/hostname": node},
            "containers": [{
                "name": "build", "image": builder_image,
                "command": ["sh", "-c", _SCRIPT],
                "env": [{"name": k, "value": v} for k, v in env.items()],
                "securityContext": {"privileged": True},
                "volumeMounts": [{"name": "containers", "mountPath": "/var/lib/containers"}],
            }],
            "volumes": [{"name": "containers", "emptyDir": {}}],
        },
    }
```

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_build_manifests.py -q`
Expected: PASS (5 tests)

- [ ] **Step 5: 커밋**

```bash
git add src/dms/build_manifests.py tests/test_build_manifests.py
git commit -m "feat(builds): 빌드 파드 매니페스트 빌더"
```

---

### Task 3: BuildRunner (k8s I/O)

**Files:**
- Create: `src/dms/build_runner.py`
- Modify: `src/dms/wiring.py`
- Test: `tests/test_build_runner.py`

**Interfaces:**
- Consumes: `K8sClient` 프로토콜 (`create`/`get`/`delete`/`read_pod_log`) from `.execution_volcano`; `ExecStatus`, `ExecutionError` from `.execution`; `build_build_pod`; `build_pod_name`.
- Produces:
  - `BuildRunner(k8s, *, namespace, registry, builder_image)`
  - `.submit(build) -> str` — ref `"buildpod/<name>"`. 실패 시 `ExecutionError("submit_failed", ...)`
  - `.poll(ref) -> ExecStatus`
  - `.read_log(ref) -> str | None`
  - `.terminate(ref) -> None` (멱등)
  - `StubBuildRunner` — 클러스터 없이 도는 테스트/개발용
  - `build_build_runner(settings) -> BuildRunner | StubBuildRunner` in `wiring.py`

`build` 인자는 `BuildsRepository.get`이 돌려주는 dict다 (`build_id`, `repo_url`, `git_ref`, `images`, `node_name`).

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_build_runner.py`:

```python
import pytest
from dms.build_runner import BuildRunner, StubBuildRunner
from dms.execution import ExecStatus, ExecutionError


class _FakeK8s:
    def __init__(self):
        self.created = []
        self.deleted = []
        self._objs = {}
        self.fail_create = False
        self.log = "hello"

    def create(self, manifest):
        if self.fail_create:
            raise RuntimeError("boom")
        self.created.append(manifest)
        self._objs[(manifest["kind"], manifest["metadata"]["name"])] = manifest

    def set_status(self, name, status):
        self._objs.setdefault(("Pod", name), {"kind": "Pod"})["status"] = status

    def get(self, kind, name, namespace):
        return self._objs.get((kind, name))

    def delete(self, kind, name, namespace):
        self.deleted.append((kind, name))
        self._objs.pop((kind, name), None)

    def read_pod_log(self, name, namespace):
        return self.log


BUILD = {"build_id": "0123456789abcdef0123456789abcdef", "repo_url": "u",
         "git_ref": "main", "images": ["dms"], "node_name": "dms-w1"}


def _runner(k8s):
    return BuildRunner(k8s, namespace="dms", registry="pkg-01:5000",
                       builder_image="quay.io/buildah/stable:latest")


def test_submit_creates_pod_and_returns_buildpod_ref():
    k8s = _FakeK8s()
    ref = _runner(k8s).submit(BUILD)
    assert ref == "buildpod/dms-build-0123456789ab"
    assert k8s.created[0]["kind"] == "Pod"


def test_submit_failure_becomes_execution_error():
    k8s = _FakeK8s()
    k8s.fail_create = True
    with pytest.raises(ExecutionError) as e:
        _runner(k8s).submit(BUILD)
    assert e.value.reason_code == "submit_failed"


@pytest.mark.parametrize("phase,expected", [
    ("Pending", ExecStatus.PENDING), ("Running", ExecStatus.RUNNING),
    ("Succeeded", ExecStatus.SUCCEEDED), ("Failed", ExecStatus.FAILED),
    ("Unknown", ExecStatus.FAILED)])
def test_poll_maps_pod_phase(phase, expected):
    k8s = _FakeK8s()
    ref = _runner(k8s).submit(BUILD)
    k8s.set_status("dms-build-0123456789ab", {"phase": phase})
    assert _runner(k8s).poll(ref) == expected


def test_poll_treats_missing_pod_as_failed():
    # 어댑터 규약과 같다: 사라진 객체는 '모름'이 아니라 실패다
    k8s = _FakeK8s()
    assert _runner(k8s).poll("buildpod/gone") == ExecStatus.FAILED


def test_read_log_returns_text_and_none_when_unavailable():
    k8s = _FakeK8s()
    ref = _runner(k8s).submit(BUILD)
    assert _runner(k8s).read_log(ref) == "hello"

    class _Boom(_FakeK8s):
        def read_pod_log(self, name, namespace):
            raise RuntimeError("gone")
    assert _runner(_Boom()).read_log(ref) is None


def test_terminate_is_idempotent():
    k8s = _FakeK8s()
    ref = _runner(k8s).submit(BUILD)
    r = _runner(k8s)
    r.terminate(ref)
    r.terminate(ref)
    assert k8s.deleted[0] == ("Pod", "dms-build-0123456789ab")


def test_non_buildpod_ref_is_rejected():
    with pytest.raises(ExecutionError) as e:
        _runner(_FakeK8s()).poll("vcjob/whatever")
    assert e.value.reason_code == "invalid_build_ref"


def test_stub_runner_runs_without_a_cluster():
    stub = StubBuildRunner()
    ref = stub.submit(BUILD)
    assert ref.startswith("buildpod/")
    assert stub.poll(ref) == ExecStatus.SUCCEEDED
    assert stub.read_log(ref) is not None
    stub.terminate(ref)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_build_runner.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'dms.build_runner'`

- [ ] **Step 3: 구현한다**

`src/dms/build_runner.py`:

```python
"""빌드 파드의 k8s I/O. 실행 어댑터(ExecutionAdapter)와 프로토콜을 공유하지 않는다 --
빌드는 JobSpec 도 큐도 아티팩트도 없어서 그 계약에 억지로 끼우면 빈 dict 만 늘어난다."""
import logging

from .build_manifests import build_build_pod
from .execution import ExecStatus, ExecutionError
from .repositories.builds import build_pod_name

logger = logging.getLogger(__name__)

_PREFIX = "buildpod"
_POD_PHASE = {"Pending": ExecStatus.PENDING, "Running": ExecStatus.RUNNING,
              "Succeeded": ExecStatus.SUCCEEDED, "Failed": ExecStatus.FAILED,
              "Unknown": ExecStatus.FAILED}


def _name(ref: str) -> str:
    prefix, _, name = ref.partition("/")
    if prefix != _PREFIX or not name:
        raise ExecutionError("invalid_build_ref", ref[:200])
    return name


class BuildRunner:
    def __init__(self, k8s, *, namespace, registry, builder_image):
        self._k8s = k8s
        self._ns = namespace
        self._registry = registry
        self._builder_image = builder_image

    def submit(self, build) -> str:
        manifest = build_build_pod(
            build_id=build["build_id"], repo_url=build["repo_url"],
            git_ref=build["git_ref"], images=build["images"],
            node=build["node_name"], namespace=self._ns,
            registry=self._registry, builder_image=self._builder_image)
        try:
            self._k8s.create(manifest)
        except Exception as exc:
            raise ExecutionError("submit_failed", str(exc)[:200]) from exc
        return f"{_PREFIX}/{manifest['metadata']['name']}"

    def poll(self, ref) -> ExecStatus:
        obj = self._k8s.get("Pod", _name(ref), self._ns)
        if obj is None:
            return ExecStatus.FAILED
        return _POD_PHASE.get((obj.get("status") or {}).get("phase"), ExecStatus.FAILED)

    def read_log(self, ref):
        try:
            return self._k8s.read_pod_log(_name(ref), self._ns)
        except ExecutionError:
            raise
        except Exception as exc:
            logger.warning("build log read failed ref=%s: %s", ref, exc)
            return None

    def terminate(self, ref) -> None:
        try:
            self._k8s.delete("Pod", _name(ref), self._ns)
        except ExecutionError:
            raise
        except Exception as exc:
            raise ExecutionError("terminate_failed", str(exc)[:200]) from exc


class StubBuildRunner:
    """클러스터가 없을 때(execution_backend != "volcano") 쓰는 결정적 페어."""
    def __init__(self):
        self._log = {}

    def submit(self, build) -> str:
        ref = f"{_PREFIX}/{build_pod_name(build['build_id'])}"
        self._log[ref] = "DMS_COMMIT_SHA=stubcommit\nDMS_BUILD_OK\n"
        return ref

    def poll(self, ref) -> ExecStatus:
        _name(ref)
        return ExecStatus.SUCCEEDED

    def read_log(self, ref):
        return self._log.get(ref, "")

    def terminate(self, ref) -> None:
        _name(ref)
        self._log.pop(ref, None)
```

- [ ] **Step 4: wiring에 붙인다**

`src/dms/wiring.py`에 더한다:

```python
def build_build_runner(settings):
    if settings.execution_backend != "volcano":
        from .build_runner import StubBuildRunner
        return StubBuildRunner()
    from .build_runner import BuildRunner
    from .execution_volcano import KubernetesClient
    return BuildRunner(KubernetesClient(settings.k8s_namespace),
                       namespace=settings.k8s_namespace,
                       registry=settings.build_registry,
                       builder_image=settings.build_builder_image)
```

`src/dms/config.py`의 `Settings`에 필드 3개와 `_SERVER_INT_KEYS` 항목 1개를 더한다:

```python
    build_registry: str = "pkg-01:5000"
    build_builder_image: str = "quay.io/buildah/stable:latest"
    build_repo_url: str = "https://github.com/ChahwanSong/dms.git"
    build_watcher_interval_seconds: int = 15
```

`from_env`에서 문자열 셋은 `environ.get("DMS_BUILD_REGISTRY", "pkg-01:5000")` 식으로 읽고, 정수는 `_SERVER_INT_KEYS`에 `("DMS_BUILD_WATCHER_INTERVAL_SECONDS", "build_watcher_interval_seconds", 15)` 를 더한다.

- [ ] **Step 5: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_build_runner.py -q`
Expected: PASS (12 tests — parametrize 5개 포함)

- [ ] **Step 6: 커밋**

```bash
git add src/dms/build_runner.py src/dms/wiring.py src/dms/config.py tests/test_build_runner.py
git commit -m "feat(builds): 빌드 파드 실행기와 stub 페어"
```

---

### Task 4: BuildWatcher 루프 + pod GC 확장

**Files:**
- Create: `src/dms/build_watcher.py`
- Modify: `src/dms/controller.py`
- Modify: `src/dms/pod_gc.py`
- Test: `tests/test_build_watcher.py`

**Interfaces:**
- Consumes: `Repositories`(`.builds`), `BuildRunner`/`StubBuildRunner`, `ExecStatus`, `ExecutionError`, `build_pod_name`.
- Produces: `BuildWatcher(repos, runner).run_once() -> dict` (`{"submitted": n, "finished": n}`)

동작:
1. `repos.builds.pending()`의 각 빌드에 대해 `runner.submit(build)` → 성공이면 `mark_running`, `ExecutionError`면 그 자리에서 `finish(state="Failed", reason_code=e.reason_code)`.
2. `repos.builds.running()`의 각 빌드에 대해 `runner.poll(ref)`. 종단이면 로그를 읽어 `DMS_COMMIT_SHA=` 마커에서 commit을 뽑고 `finish(...)`.
   - `ExecStatus.SUCCEEDED` → `state="Succeeded"`
   - 그 외 종단 → `state="Failed"`, `reason_code="build_failed"`
   - ref는 저장하지 않고 `build_pod_name(build_id)`로 **재구성**한다 (결정적이라 컬럼이 필요 없다).

`PodGarbageCollector.run_once()`가 종단 빌드의 파드도 수거하게 한다 — 단 `runner`가 주어졌을 때만.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_build_watcher.py`:

```python
import pytest
from dms.build_watcher import BuildWatcher, parse_commit_sha
from dms.db import Database
from dms.execution import ExecStatus, ExecutionError
from dms.migrations import migrate
from dms.repositories import Repositories


class _Runner:
    def __init__(self, status=ExecStatus.SUCCEEDED, log="DMS_COMMIT_SHA=deadbeef\n"):
        self.status = status
        self.log = log
        self.submitted = []
        self.fail_submit = None

    def submit(self, build):
        if self.fail_submit:
            raise ExecutionError(self.fail_submit, "nope")
        self.submitted.append(build["build_id"])
        return f"buildpod/dms-build-{build['build_id'][:12]}"

    def poll(self, ref):
        return self.status

    def read_log(self, ref):
        return self.log

    def terminate(self, ref):
        pass


@pytest.fixture
def repos(tmp_path):
    db = Database(f"sqlite:///{tmp_path}/t.db")
    migrate(db)
    return Repositories(db)


def _mk(repos):
    return repos.builds.create(repo_url="u", git_ref="main", images=["dms"],
                               node_name="dms-w1", actor="a")


def test_pending_build_is_submitted_and_becomes_running(repos):
    bid = _mk(repos)
    runner = _Runner(status=ExecStatus.RUNNING)
    out = BuildWatcher(repos, runner).run_once()
    assert out["submitted"] == 1
    assert runner.submitted == [bid]
    assert repos.builds.get(bid)["state"] == "Running"


def test_submit_failure_is_recorded_as_failed_not_raised(repos):
    # 루프 예외는 상위에서 삼켜진다 -- 실패는 반드시 DB 상태로 드러나야 한다
    bid = _mk(repos)
    runner = _Runner()
    runner.fail_submit = "submit_failed"
    BuildWatcher(repos, runner).run_once()
    row = repos.builds.get(bid)
    assert (row["state"], row["reason_code"]) == ("Failed", "submit_failed")


def test_running_build_finishes_and_captures_commit_and_log(repos):
    bid = _mk(repos)
    repos.builds.mark_running(bid)
    runner = _Runner(status=ExecStatus.SUCCEEDED,
                     log="=== building ===\nDMS_COMMIT_SHA=deadbeef1234\nDMS_BUILD_OK\n")
    out = BuildWatcher(repos, runner).run_once()
    assert out["finished"] == 1
    row = repos.builds.get(bid)
    assert row["state"] == "Succeeded"
    assert row["commit_sha"] == "deadbeef1234"
    assert "DMS_BUILD_OK" in row["log_text"]


def test_failed_pod_becomes_failed_build(repos):
    bid = _mk(repos)
    repos.builds.mark_running(bid)
    BuildWatcher(repos, _Runner(status=ExecStatus.FAILED)).run_once()
    row = repos.builds.get(bid)
    assert (row["state"], row["reason_code"]) == ("Failed", "build_failed")


def test_running_build_stays_running_while_pod_runs(repos):
    bid = _mk(repos)
    repos.builds.mark_running(bid)
    out = BuildWatcher(repos, _Runner(status=ExecStatus.RUNNING)).run_once()
    assert out["finished"] == 0
    assert repos.builds.get(bid)["state"] == "Running"


def test_run_once_is_idempotent_on_terminal_builds(repos):
    bid = _mk(repos)
    repos.builds.mark_running(bid)
    w = BuildWatcher(repos, _Runner())
    w.run_once()
    before = repos.builds.get(bid)
    w.run_once()
    assert repos.builds.get(bid) == before


@pytest.mark.parametrize("text,expected", [
    ("DMS_COMMIT_SHA=abc123\n", "abc123"),
    ("noise\nDMS_COMMIT_SHA=abc123\nmore\n", "abc123"),
    ("no marker here", None),
    ("DMS_COMMIT_SHA=\n", None),
])
def test_parse_commit_sha(text, expected):
    assert parse_commit_sha(text) == expected


def test_parse_commit_sha_handles_none():
    assert parse_commit_sha(None) is None
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_build_watcher.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'dms.build_watcher'`

- [ ] **Step 3: 구현한다**

`src/dms/build_watcher.py`:

```python
"""빌드 상태를 파드에서 DB로 옮기는 컨트롤러 루프.

루프 안의 예외는 controller.run_all_once 가 삼켜 stderr 로만 내보낸다 --
그래서 실패는 예외로 새지 않고 반드시 builds.state 로 드러나야 한다."""
import logging

from .execution import ExecStatus, ExecutionError
from .repositories.builds import build_pod_name

logger = logging.getLogger(__name__)

_MARKER = "DMS_COMMIT_SHA="
_TERMINAL = (ExecStatus.SUCCEEDED, ExecStatus.FAILED, ExecStatus.TIMED_OUT)


def parse_commit_sha(log_text):
    """빌드 스크립트가 찍는 DMS_COMMIT_SHA= 마커에서 커밋을 뽑는다.
    로그 형식을 파싱하지 않고 마커 한 줄만 본다 -- buildah 출력은 언제든 바뀐다."""
    if not log_text:
        return None
    for line in log_text.split("\n"):
        line = line.strip()
        if line.startswith(_MARKER):
            value = line[len(_MARKER):].strip()
            if value:
                return value
    return None


class BuildWatcher:
    def __init__(self, repos, runner):
        self._repos = repos
        self._runner = runner

    def _ref(self, build):
        return f"buildpod/{build_pod_name(build['build_id'])}"

    def run_once(self) -> dict:
        submitted = finished = 0
        for build in self._repos.builds.pending():
            try:
                self._runner.submit(build)
            except ExecutionError as exc:
                self._repos.builds.finish(build["build_id"], state="Failed",
                                          reason_code=exc.reason_code)
                continue
            self._repos.builds.mark_running(build["build_id"])
            submitted += 1

        for build in self._repos.builds.running():
            ref = self._ref(build)
            try:
                status = self._runner.poll(ref)
            except ExecutionError as exc:
                self._repos.builds.finish(build["build_id"], state="Failed",
                                          reason_code=exc.reason_code)
                finished += 1
                continue
            if status not in _TERMINAL:
                continue
            log_text = self._runner.read_log(ref)
            self._repos.builds.finish(
                build["build_id"],
                state="Succeeded" if status == ExecStatus.SUCCEEDED else "Failed",
                reason_code=None if status == ExecStatus.SUCCEEDED else "build_failed",
                commit_sha=parse_commit_sha(log_text),
                log_text=log_text)
            finished += 1
        return {"submitted": submitted, "finished": finished}
```

- [ ] **Step 4: 컨트롤러에 루프를 등록한다**

`src/dms/controller.py`의 `build_loops` 시그니처에 `build_runner=None` 을 더하고, 리스트 끝에 루프를 추가한다:

```python
        Loop("build-watcher", settings.build_watcher_interval_seconds,
             lambda: BuildWatcher(repos, build_runner).run_once()),
```

`build_runner`가 `None`이면 이 루프를 리스트에 넣지 않는다 (stub 조차 없는 호출자를 깨뜨리지 않기 위해). `run_forever`도 `build_runner`를 받아 넘긴다.

- [ ] **Step 5: pod GC를 넓힌다**

`src/dms/pod_gc.py`의 `PodGarbageCollector.__init__`에 `build_runner=None`, `builds_repo_getter` 대신 `repos`를 이미 갖고 있으므로 `run_once` 끝에 더한다:

```python
        # 종단 빌드가 남긴 빌드 파드도 같은 창(after_seconds)으로 수거한다.
        # 비종단 빌드는 절대 건드리지 않는다 -- 파드가 사라지면 poll 이 FAILED 로 읽는다.
        if self._build_runner is not None:
            for build in self._repos.builds.terminal_older_than(
                    self._after, limit=self._limit, now_iso=now_iso):
                ref = f"buildpod/{build_pod_name(build['build_id'])}"
                try:
                    self._build_runner.terminate(ref)
                    deleted += 1
                except Exception as exc:
                    logger.warning("build pod gc failed ref=%s: %s", ref, exc)
```

`controller.py`의 pod-gc 루프도 `build_runner=build_runner`를 넘기게 고친다.

- [ ] **Step 6: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_build_watcher.py tests/test_pod_gc.py -q`
Expected: PASS. `tests/test_pod_gc.py`가 없으면 pod_gc 테스트 파일 이름을 `ls tests | grep gc` 로 찾아 쓴다.

- [ ] **Step 7: 전체 스위트**

Run: `.venv/bin/python -m pytest -q`
Expected: 전부 PASS

- [ ] **Step 8: 커밋**

```bash
git add src/dms/build_watcher.py src/dms/controller.py src/dms/pod_gc.py tests/test_build_watcher.py
git commit -m "feat(builds): 빌드 감시 루프와 빌드 파드 GC"
```

---

### Task 5: admin 빌드 API

**Files:**
- Create: `src/dms/api/routes_builds.py`
- Modify: `src/dms/api/routes_control.py`
- Modify: `src/dms/api/app.py`
- Test: `tests/test_api_builds.py`

**Interfaces:**
- Consumes: `require_admin`, `Identity` from `.auth`; `reject_when_maintenance` from `.routes_requests`; `BuildsRepository`; `BUILD_IMAGES`, `build_tag`, `build_pod_name`; `tail_lines` from `.artifacts`.
- Produces: 4개 엔드포인트 + `ControlStateBody.build_node_name`.

| 메서드 | 경로 | 응답 |
|---|---|---|
| POST | `/api/admin/builds` | `202 {"build_id", "state"}` |
| GET | `/api/admin/builds` | `[{...}]` (최신순) |
| GET | `/api/admin/builds/{build_id}` | 상세 + `tag` |
| GET | `/api/admin/builds/{build_id}/log` | `{"build_id", "log"}` |

거부:
- 유지보수 중 제출 → `reject_when_maintenance` (기존 동작)
- `control_state.build_node_name`이 비어 있으면 → `422 build_node_not_set`
- `images`가 비었거나 `BUILD_IMAGES` 밖이면 → `422 unknown_image`
- `git_ref`가 비었거나 공백/제어문자를 포함하면 → `422 invalid_git_ref`
- 이미 `active()` 빌드가 있으면 → `409 build_in_progress`
- 없는 build_id → `404 build_not_found`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_api_builds.py`. 기존 API 테스트가 앱/클라이언트를 어떻게 만드는지 먼저 본다:
`ls tests | grep api_` 로 가장 가까운 파일(예: `tests/test_api_policies.py`)을 열어 픽스처 패턴을 그대로 따른다. 아래 테스트는 그 픽스처 이름을 그대로 쓴다고 가정한다 — 다르면 맞춘다.

```python
def test_submit_requires_build_node(admin_client):
    r = admin_client.post("/api/admin/builds", json={"git_ref": "main", "images": ["dms"]})
    assert r.status_code == 422 and r.json()["detail"] == "build_node_not_set"


def test_submit_accepted_once_node_is_set(admin_client, repos):
    repos.control.set_control_state(maintenance=False, drain=False, reason=None,
                                    build_node_name="dms-w1", actor="ops")
    r = admin_client.post("/api/admin/builds", json={"git_ref": "main", "images": ["dms"]})
    assert r.status_code == 202
    body = r.json()
    assert body["state"] == "Pending" and body["build_id"]


def test_second_concurrent_submit_is_rejected(admin_client, repos):
    repos.control.set_control_state(maintenance=False, drain=False, reason=None,
                                    build_node_name="dms-w1", actor="ops")
    admin_client.post("/api/admin/builds", json={"git_ref": "main", "images": ["dms"]})
    r = admin_client.post("/api/admin/builds", json={"git_ref": "main", "images": ["dms"]})
    assert r.status_code == 409 and r.json()["detail"] == "build_in_progress"


def test_unknown_image_is_rejected(admin_client, repos):
    repos.control.set_control_state(maintenance=False, drain=False, reason=None,
                                    build_node_name="dms-w1", actor="ops")
    r = admin_client.post("/api/admin/builds", json={"git_ref": "main", "images": ["nope"]})
    assert r.status_code == 422 and r.json()["detail"] == "unknown_image"


def test_empty_images_is_rejected(admin_client, repos):
    repos.control.set_control_state(maintenance=False, drain=False, reason=None,
                                    build_node_name="dms-w1", actor="ops")
    r = admin_client.post("/api/admin/builds", json={"git_ref": "main", "images": []})
    assert r.status_code == 422 and r.json()["detail"] == "unknown_image"


def test_bad_git_ref_is_rejected(admin_client, repos):
    repos.control.set_control_state(maintenance=False, drain=False, reason=None,
                                    build_node_name="dms-w1", actor="ops")
    r = admin_client.post("/api/admin/builds", json={"git_ref": "ma in", "images": ["dms"]})
    assert r.status_code == 422 and r.json()["detail"] == "invalid_git_ref"


def test_detail_exposes_the_tag_that_will_be_pushed(admin_client, repos):
    repos.control.set_control_state(maintenance=False, drain=False, reason=None,
                                    build_node_name="dms-w1", actor="ops")
    bid = admin_client.post("/api/admin/builds",
                            json={"git_ref": "main", "images": ["dms"]}).json()["build_id"]
    r = admin_client.get(f"/api/admin/builds/{bid}")
    assert r.status_code == 200
    assert r.json()["tag"] == "b" + bid[:8]
    assert r.json()["images"] == ["dms"]


def test_missing_build_is_404(admin_client):
    assert admin_client.get("/api/admin/builds/nope").status_code == 404
    assert admin_client.get("/api/admin/builds/nope/log").status_code == 404


def test_list_is_admin_only(user_client):
    assert user_client.get("/api/admin/builds").status_code in (401, 403)


def test_control_state_accepts_build_node(admin_client):
    r = admin_client.put("/api/admin/control-state",
                         json={"maintenance": False, "drain": False, "reason": None,
                               "build_node_name": "dms-w2"})
    assert r.status_code == 200 and r.json()["build_node_name"] == "dms-w2"
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_api_builds.py -q`
Expected: FAIL — 404 (라우트 없음)

- [ ] **Step 3: 라우터를 만든다**

`src/dms/api/routes_builds.py`:

```python
import re

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from ..repositories.builds import BUILD_IMAGES, build_tag
from .artifacts import tail_lines
from .auth import Identity, require_admin
from .routes_requests import reject_when_maintenance

router = APIRouter(dependencies=[Depends(require_admin)])

# git ref: 공백·제어문자·'..'·선행 '-' 를 막는다. 이 값은 파드 env 로 흘러가
# `git clone --branch "$DMS_BUILD_REF"` 에 쓰인다.
_REF_RE = re.compile(r"[A-Za-z0-9._/-]{1,200}")


class BuildBody(BaseModel):
    git_ref: str
    images: list[str]
    repo_url: str | None = None


def _detail(row):
    out = dict(row)
    out["tag"] = build_tag(row["build_id"])
    return out


@router.post("/api/admin/builds", status_code=202)
def submit_build(body: BuildBody, request: Request,
                 identity: Identity = Depends(require_admin)):
    reject_when_maintenance(request)
    repos = request.app.state.repos
    node = (repos.control.control_state() or {}).get("build_node_name")
    if not node:
        raise HTTPException(status_code=422, detail="build_node_not_set")
    if not body.images or any(i not in BUILD_IMAGES for i in body.images):
        raise HTTPException(status_code=422, detail="unknown_image")
    ref = (body.git_ref or "").strip()
    if not _REF_RE.fullmatch(ref) or ".." in ref or ref.startswith("-"):
        raise HTTPException(status_code=422, detail="invalid_git_ref")
    if repos.builds.active() is not None:
        raise HTTPException(status_code=409, detail="build_in_progress")
    build_id = repos.builds.create(
        repo_url=body.repo_url or request.app.state.settings.build_repo_url,
        git_ref=ref, images=list(body.images), node_name=node,
        actor=identity.actor)
    return {"build_id": build_id, "state": "Pending"}


@router.get("/api/admin/builds")
def list_builds(request: Request, limit: int = Query(default=50, ge=1, le=200)):
    return [_detail(r) for r in request.app.state.repos.builds.list(limit=limit)]


@router.get("/api/admin/builds/{build_id}")
def get_build(build_id: str, request: Request):
    row = request.app.state.repos.builds.get(build_id)
    if row is None:
        raise HTTPException(status_code=404, detail="build_not_found")
    return _detail(row)


@router.get("/api/admin/builds/{build_id}/log")
def get_build_log(build_id: str, request: Request,
                  tail: int | None = Query(default=None, ge=1)):
    repos = request.app.state.repos
    row = repos.builds.get(build_id)
    if row is None:
        raise HTTPException(status_code=404, detail="build_not_found")
    runner = getattr(request.app.state, "build_runner", None)
    log = None
    if row["state"] in ("Pending", "Running") and runner is not None:
        # 진행 중이면 파드에서 실시간으로 읽는다. 종단이면 박제된 사본이 진실이다
        # -- 파드는 GC 되어 사라질 수 있다.
        from ..repositories.builds import build_pod_name
        log = runner.read_log(f"buildpod/{build_pod_name(build_id)}")
    if log is None:
        log = row.get("log_text")
    if log is not None and tail is not None:
        log = tail_lines(log, tail)
    return {"build_id": build_id, "log": log}
```

- [ ] **Step 4: control-state에 필드를 더한다**

`src/dms/api/routes_control.py`:

```python
class ControlStateBody(BaseModel):
    maintenance: bool
    drain: bool
    reason: str | None = None
    build_node_name: str | None = None
```

그리고 `set_control_state(..., build_node_name=body.build_node_name)`.

- [ ] **Step 5: 앱에 등록한다**

`src/dms/api/app.py`에서 `from .routes_builds import router as builds_router` 후 `app.include_router(builds_router)`. 또한 앱 생성 시 `app.state.build_runner = build_build_runner(settings)` 를 설정한다 (`execution_adapter`를 두는 곳과 같은 자리). 테스트에서 앱을 만들 때 stub이 들어가도록 `wiring.build_build_runner`를 쓴다.

- [ ] **Step 6: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_api_builds.py -q`
Expected: PASS (11 tests)

- [ ] **Step 7: 전체 스위트**

Run: `.venv/bin/python -m pytest -q`
Expected: 전부 PASS

- [ ] **Step 8: 커밋**

```bash
git add src/dms/api/routes_builds.py src/dms/api/routes_control.py src/dms/api/app.py tests/test_api_builds.py
git commit -m "feat(api): admin 빌드 엔드포인트와 빌드 노드 지정"
```

---

### Task 6: 포탈 「빌드」 화면

**Files:**
- Create: `frontend/src/features/builds/useBuilds.ts`
- Create: `frontend/src/features/builds/BuildsPage.tsx`
- Create: `frontend/src/features/builds/BuildsPage.test.tsx`
- Modify: `frontend/src/lib/types.ts`, `frontend/src/lib/api.ts`
- Modify: `frontend/src/app/router.tsx`, `frontend/src/app/AppShell.tsx`
- Modify: `frontend/src/features/control/ControlStatePage.tsx`, `useControlState.ts`

**Interfaces:**
- Consumes: `apiGet`, `apiSend`, `ApiError`, `REASON_MESSAGES` from `../../lib/api`; `Card`, `Button` from `../../components/ui/*`.
- Produces: `useBuilds()`, `useBuild(id)`, `useSubmitBuild()`, `BuildsPage`.

- [ ] **Step 1: 타입과 사유 코드를 더한다**

`frontend/src/lib/types.ts`에:

```ts
export interface Build {
  build_id: string; repo_url: string; git_ref: string; commit_sha: string | null;
  images: string[]; node_name: string; state: string; reason_code: string | null;
  tag: string; created_at: string; finished_at: string | null;
}
```

그리고 `ControlState`에 `build_node_name: string | null;` 을 더한다.

`frontend/src/lib/api.ts`의 `REASON_MESSAGES`에:

```ts
  build_node_not_set: "빌드 노드가 지정되지 않았습니다 — 컨트롤 상태에서 먼저 지정하세요",
  build_in_progress: "이미 진행 중인 빌드가 있습니다",
  unknown_image: "빌드할 이미지를 선택해 주세요",
  invalid_git_ref: "git ref 형식이 올바르지 않습니다",
  build_not_found: "빌드를 찾을 수 없습니다",
  build_failed: "빌드가 실패했습니다 — 로그를 확인하세요",
  submit_failed: "빌드 파드를 만들지 못했습니다",
```

- [ ] **Step 2: 실패하는 테스트를 쓴다**

`frontend/src/features/builds/BuildsPage.test.tsx`. 옆 파일(`features/policies/PoliciesList.test.tsx`)의 MSW/QueryClient 래퍼 패턴을 그대로 따른다.

```tsx
import { describe, it, expect, beforeAll, afterEach, afterAll } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { BuildsPage } from "./BuildsPage";

const BUILD = {
  build_id: "0123456789abcdef0123456789abcdef", repo_url: "u", git_ref: "main",
  commit_sha: "deadbeef", images: ["dms"], node_name: "dms-w1",
  state: "Succeeded", reason_code: null, tag: "b01234567",
  created_at: "2026-08-06T00:00:00Z", finished_at: "2026-08-06T00:10:00Z",
};

const server = setupServer(
  http.get("/api/admin/control-state", () =>
    HttpResponse.json({ maintenance: 0, drain: 0, reason: null,
                        build_node_name: "dms-w1", changed_by: "ops",
                        changed_at: "2026-08-06T00:00:00Z" })),
  http.get("/api/admin/builds", () => HttpResponse.json([BUILD])),
);
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const wrap = (ui: React.ReactNode) => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
};

describe("BuildsPage", () => {
  it("빌드 목록을 렌더한다", async () => {
    wrap(<BuildsPage />);
    expect(await screen.findByText("b01234567")).toBeInTheDocument();
    expect(screen.getByText("dms-w1")).toBeInTheDocument();
  });

  it("빌드 노드가 없으면 제출을 막고 안내한다", async () => {
    server.use(http.get("/api/admin/control-state", () =>
      HttpResponse.json({ maintenance: 0, drain: 0, reason: null,
                          build_node_name: null, changed_by: null, changed_at: null })));
    wrap(<BuildsPage />);
    expect(await screen.findByText(/빌드 노드가 지정되지 않았습니다/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "빌드 시작" })).toBeDisabled();
  });

  it("이미지를 하나도 고르지 않으면 제출 버튼이 비활성이다", async () => {
    wrap(<BuildsPage />);
    await screen.findByText("b01234567");
    await userEvent.click(screen.getByLabelText("dms"));   // 기본 체크를 끈다
    expect(screen.getByRole("button", { name: "빌드 시작" })).toBeDisabled();
  });

  it("제출하면 POST 하고 목록을 다시 읽는다", async () => {
    let posted: unknown = null;
    server.use(http.post("/api/admin/builds", async ({ request }) => {
      posted = await request.json();
      return HttpResponse.json({ build_id: "x", state: "Pending" }, { status: 202 });
    }));
    wrap(<BuildsPage />);
    await screen.findByText("b01234567");
    await userEvent.click(screen.getByRole("button", { name: "빌드 시작" }));
    await waitFor(() => expect(posted).toEqual({ git_ref: "main", images: ["dms"] }));
  });

  it("서버 오류를 한국어 메시지로 보여준다", async () => {
    server.use(http.post("/api/admin/builds", () =>
      HttpResponse.json({ detail: "build_in_progress" }, { status: 409 })));
    wrap(<BuildsPage />);
    await screen.findByText("b01234567");
    await userEvent.click(screen.getByRole("button", { name: "빌드 시작" }));
    expect(await screen.findByText("이미 진행 중인 빌드가 있습니다")).toBeInTheDocument();
  });

  it("목록이 배열이 아니어도 흰 화면이 되지 않는다", async () => {
    server.use(http.get("/api/admin/builds", () => HttpResponse.json({ oops: true })));
    wrap(<BuildsPage />);
    expect(await screen.findByRole("heading", { name: "빌드" })).toBeInTheDocument();
  });
});
```

- [ ] **Step 3: 실패를 확인한다**

Run: `cd frontend && npx vitest run src/features/builds/BuildsPage.test.tsx`
Expected: FAIL — `Failed to resolve import "./BuildsPage"`

- [ ] **Step 4: 훅을 만든다**

`frontend/src/features/builds/useBuilds.ts`:

```ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "../../lib/api";
import type { Build } from "../../lib/types";

export const useBuilds = () =>
  useQuery({
    queryKey: ["builds"],
    queryFn: () => apiGet<Build[]>("/api/admin/builds"),
    // 진행 중인 빌드는 몇 분씩 걸린다 — 주기적으로 다시 읽어 상태를 따라간다.
    refetchInterval: 5000,
  });

export interface SubmitBuildBody { git_ref: string; images: string[]; }

export const useSubmitBuild = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: SubmitBuildBody) => apiSend("POST", "/api/admin/builds", b),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["builds"] }),
  });
};
```

- [ ] **Step 5: 화면을 만든다**

`frontend/src/features/builds/BuildsPage.tsx` — h1은 정확히 `빌드`. 이미지 체크박스는 `dms-mpifileutils` / `dms` / `dms-agent`, 기본은 `dms`만 체크. `aria-label`은 이미지 이름 그대로. 제출 버튼 이름은 `빌드 시작`. 목록은 `Array.isArray(q.data) ? q.data : []` 로 정규화한다. 빌드 노드는 `useControlState()`에서 읽고, 없으면 안내 문구 + 버튼 비활성.

표 컬럼: 시각 / ref / commit(앞 8자) / 이미지 / 노드 / 태그 / 상태. null은 `—`.

- [ ] **Step 6: 라우트와 내비를 등록한다**

`router.tsx`에 import와 함께:

```tsx
        <Route path="/admin/builds" element={<RequireRole role="admin"><AppShell><BuildsPage /></AppShell></RequireRole>} />
```

`AppShell.tsx`에:

```tsx
        {isAdmin && <NavLink to="/admin/builds" className={linkCls}>빌드</NavLink>}
```

- [ ] **Step 7: 컨트롤 상태 화면에 빌드 노드 입력을 더한다**

`useControlState.ts`의 `ControlStateBody`에 `build_node_name: string | null;` 을 더하고, `ControlStatePage.tsx`에 입력 필드(`aria-label="빌드 노드"`)를 추가해 `submit()`에서 함께 보낸다. 기존 `ControlStatePage.test.tsx`가 깨지면 그 테스트의 PUT 기대값에 새 필드를 반영한다.

- [ ] **Step 8: 통과를 확인한다**

Run: `cd frontend && npx vitest run` 그리고 `npx tsc -b`
Expected: 전체 PASS, 타입 에러 0

- [ ] **Step 9: 커밋**

```bash
git add frontend/src
git commit -m "feat(portal): 빌드 화면과 빌드 노드 지정"
```

---

### Task 7: 배포 매니페스트와 문서

**Files:**
- Modify: `deploy/k8s/20-config.yaml`
- Modify: `deploy/k8s/30-migrate-job.yaml`, `40-api.yaml`, `41-controller.yaml`
- Modify: `deploy/README.md`

**Interfaces:**
- Consumes: Task 3에서 더한 `DMS_BUILD_*` 설정 이름.
- Produces: 배포 가능한 d21 매니페스트.

- [ ] **Step 1: ConfigMap에 빌드 설정을 더한다**

`deploy/k8s/20-config.yaml`의 ConfigMap `data`에:

```yaml
  # --- 포탈 주도 이미지 빌드 (슬라이스 11) ---
  # 빌드 노드는 DB(control_state.build_node_name)에 있다 -- 운영자가 포탈에서 바꾸므로
  # 여기 두면 재적용마다 되돌아간다.
  DMS_BUILD_REGISTRY: "pkg-01:5000"
  DMS_BUILD_BUILDER_IMAGE: "quay.io/buildah/stable:latest"
  DMS_BUILD_REPO_URL: "https://github.com/ChahwanSong/dms.git"
  DMS_BUILD_WATCHER_INTERVAL_SECONDS: "15"
```

- [ ] **Step 2: 이미지 태그를 d21로 올린다**

`30-migrate-job.yaml`, `40-api.yaml`, `41-controller.yaml`의 `pkg-01:5000/dms:d20` → `d21`.
`50-agent-daemonset.yaml`과 `DMS_JOB_IMAGE`는 **건드리지 않는다** (별도 태그 계열이고 이 슬라이스에서 바뀌지 않는다).

- [ ] **Step 3: RBAC가 이미 충분한지 확인한다**

Run: `kubectl --context dms auth can-i --list --as=system:serviceaccount:dms:dms-controller -n dms`
Expected: `pods [create get list watch delete]`가 이미 있다 → **RBAC 변경 불필요**. 없으면 `deploy/k8s/10-rbac.yaml`에 추가하고 이 사실을 커밋 메시지에 적는다.

- [ ] **Step 4: README에 절차와 제약을 적는다**

`deploy/README.md`에 「포탈에서 이미지 빌드」 절을 더한다. 반드시 담을 것:

- 컨트롤 상태 화면에서 빌드 노드를 먼저 지정해야 한다는 것.
- **빌드는 GitHub에 push된 커밋만 대상으로 한다** — 로컬 커밋은 빌드되지 않는다.
- 빌드 태그는 `b<build_id 앞 8자>`이며, 모든 매니페스트가 `imagePullPolicy: IfNotPresent`라 같은 태그 재push는 반영되지 않기 때문에 빌드마다 새 태그가 나온다는 것.
- 빌드 노드는 인터넷 egress가 필요하다는 것 (npmjs, dl.k8s.io, github.com, PyPI, Debian 미러).
- 만들어진 태그를 실제로 쓰려면 매니페스트의 `image:`를 손으로 바꿔 apply해야 한다는 것 — **자동 롤아웃은 슬라이스 12** 범위다.
- `dms-mpifileutils`는 소스 컴파일이라 매우 오래 걸리므로 기본 선택이 아니라는 것.

- [ ] **Step 5: 커밋**

```bash
git add deploy/
git commit -m "deploy: 빌드 설정과 d21 태그, 포탈 빌드 절차 문서화"
```

---

## Self-Review

**1. 스펙 커버리지**

| 설계 절 | 담당 태스크 |
|---|---|
| §2 bare Pod 결정 (ref 접두 `buildpod/`) | Task 3 |
| §3 빌드 노드 지정 (`control_state.build_node_name`) | Task 1, 5, 6 |
| §4.1 이미지 3종·의존 순서·기본 `dms` | Task 1(`BUILD_IMAGES`), 2(정렬), 6(기본 체크) |
| §4.2 빌드마다 새 태그 | Task 1 (`build_tag`) |
| §4.3 privileged 파드, env 전달, insecure 레지스트리, commit 마커 | Task 2 |
| §4.4 비-hermetic·핀 노출 금지 | Task 2(핀을 인자로 노출하지 않음), Task 7(문서) |
| §5 BuildWatcher·상태기계·GC | Task 4 |
| §6 API 4종·사유 코드 | Task 5 |
| §7 포탈 화면 | Task 6 |
| §8 범위 밖(롤아웃) | 전 태스크에서 제외 — Global Constraints에 명시 |
| §9 실증 | 플랜 실행 후 별도 수행 |

**2. 플레이스홀더 점검** — "적절히 처리한다" 류 없음. 모든 코드 단계에 실제 코드가 있다. Task 6 Step 5만 산문인데, 그 화면의 계약(h1 문자열, aria-label, 버튼 이름, 정규화, 컬럼)이 Step 2의 테스트로 전부 고정돼 있어 구현이 결정된다.

**3. 타입 일관성** — `build_tag`/`build_pod_name`/`BUILD_IMAGES`는 Task 1에서 정의하고 2·3·4·5가 같은 이름으로 쓴다. ref 문자열은 `buildpod/<pod name>` 하나로 통일. `BuildsRepository.finish`의 키워드(`state`, `reason_code`, `commit_sha`, `log_text`)는 Task 4에서 그대로 쓰인다. `images`는 저장소 경계에서 항상 리스트로 정규화되어 API·프론트가 리스트만 본다.

**한 가지 알려진 위험(실증에서 드러날 것):** privileged 컨테이너 안의 buildah가 overlay 드라이버로 뜨지 못하면 빌드가 실패한다. 그 경우 로그에 드라이버 오류가 그대로 남으며, 대응은 `_SCRIPT`의 `buildah bud`에 `--storage-driver vfs`를 더하는 한 줄이다. 미리 넣지 않는 이유는 vfs가 훨씬 느리고 디스크를 많이 먹기 때문이다.
