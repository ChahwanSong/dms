# 슬라이스 13 — 포탈 주도 롤아웃 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 운영자가 포탈 「릴리스」 화면에서 빌드된 이미지 태그를 골라 제출하면, 컨트롤러가 `dms-agent` → `dms-api` → `dms-controller` 순서로 strategic-merge patch를 적용하고, 수렴 여부를 워크로드 상태에서 판정해 `releases` 행으로 드러낸다. 컨트롤러 자기 갱신(마지막 순서)은 2단계 record-then-patch로 프로세스 죽음을 넘어 이어진다.

**Architecture:** k8s I/O는 새 좁은 `WorkloadClient` Protocol 뒤에 두고(기존 `K8sClient`는 확장하지 않는다), 같은 구체 클래스 `KubernetesClient`가 구조적 타이핑으로 둘 다 만족한다. 상태 정규화(snake/camel)는 클라이언트 안에서, 수렴 판정은 순수 함수로 분리한다. `RolloutWatcher` 컨트롤러 루프가 (1) `releases` 행을 `Applying`으로 **커밋한 뒤** (2) patch를 부르고, (3) 나중 틱에 살아 있는 워크로드를 관찰해 `Applied`/`Failed`로 넘긴다 — "방금 patch를 불렀다"는 사실은 절대 근거로 쓰지 않는다. admin API는 레지스트리 태그 조회(실패 내성)와 순서 강제 배치 제출을 제공한다.

**Tech Stack:** Python 3.11 / FastAPI / SQLite+PostgreSQL 양립 SQL, React 18 + Vite + TS + Tailwind + TanStack Query v5 + Vitest/Testing Library/MSW 2, Kubernetes apps/v1 (Deployment/DaemonSet strategic merge patch) + Docker Registry v2 API.

## Global Constraints

- 설계 문서: `docs/superpowers/specs/2026-08-06-dms-portal-rollout-slice13-design.md`. 충돌하면 설계가 이긴다.
- `legacy/` 아래는 읽기 전용 — 수정·이동·삭제·추가 금지, import 금지, **코드 복사 금지**. (설계가 legacy의 수렴 판정 "모양"을 참고하라 했어도 복사는 금지 — 새로 작성한다.)
- 범위 밖(설계 §10): `DMS_JOB_IMAGE`(ConfigMap) 변경, 롤백 버튼, 매니페스트 파일 자동 수정, 모니터링 대시보드(§9 → 슬라이스 14).
- **`K8sClient` Protocol(`execution_volcano.py`)을 확장하지 마라** — 네 개의 기존 테스트 페어가 그것을 구조적으로 구현하고 있고 apps/v1 동사가 필요한 것은 하나도 없다. 새 좁은 Protocol은 별도 모듈 `src/dms/rollout_runner.py`에 둔다.
- `AppsV1Api`는 **기존 `_ensure()` 본문 안**(현재 가드 `if self._core is None:`)에서 만든다. `_apps`로 별도 가드를 만들면 `tests/test_k8s_read_pod_log.py`처럼 `_ensure`를 스텁하는 테스트 패턴이 깨진다.
- 패치는 컨테이너 `name`을 patchMergeKey로 쓰는 **strategic merge patch**다. JSON merge patch는 containers 배열 전체를 교체해 env·volumeMounts를 날린다. `_content_type="application/strategic-merge-patch+json"`을 **명시적으로** 넘긴다.
- 상태 읽기는 **하나의 키 표기로 정규화**한다 — `to_dict()`는 snake_case, 원시 dict는 camelCase. 정규화 없이는 페어는 통과하고 프로덕션만 `None`을 읽어 "영원히 수렴 안 함"이 된다.
- 404 → `None`, 그 외 API 예외는 재전파. **403은 로그에서 구분 가능해야 한다** (RBAC 거부가 "없다"와 똑같이 렌더된 `read_pod_log` 사고의 교훈).
- **완료 판정은 `observedGeneration >= metadata.generation` 게이트를 먼저 통과해야 한다** — 아니면 옛 ReplicaSet 기준 거짓 성공이 난다.
- **패치 전에 `releases` 행을 커밋**한다(record-then-patch). "행은 Applying인데 클러스터가 이미 목표 이미지"는 **정상 케이스**(patch 직후 죽음)로 취급해 Applied로 수렴시킨다. 같은 이미지 재패치는 새 ReplicaSet을 만들지 않아 멱등이다.
- 컨트롤러 루프 안 예외는 상위(`run_all_once`)에서 삼켜진다 → **실패는 `releases.state`/`reason_code`로 드러나야 한다.** `run_once()`는 멱등.
- 리스는 갱신되지 않는다(`max(interval*3, 30)`초) → **어떤 롤아웃 단계도 lease_seconds보다 오래 블록하면 안 된다.** 루프는 폴링 모양이고 매 틱 반환한다. 롤아웃 루프 간격 10초 → 리스 30초(설계 §2 타이밍).
- SQL은 `:named` 파라미터만, SQLite/PostgreSQL 양립. 기존 테이블 컬럼 추가는 `CREATE TABLE` 텍스트와 `_ensure_columns` **양쪽**.
- 저장소 클래스: 동기, `__init__(self, db)`만, `self._db` 보관, 뮤테이터 인자는 키워드 전용이며 마지막이 `actor`, `Repositories`에 등록. admin 뮤테이션은 변경과 **같은 트랜잭션 안에서** `audit_log` 행을 쓴다(`mutation_class="release"`).
- 새 백엔드 사유 코드는 `frontend/src/lib/api.ts`의 `REASON_MESSAGES`와 `frontend/src/lib/reasonCodes.json` **양쪽**에 넣는다. `tests/test_reason_codes_coverage.py`가 `src/dms/`의 리터럴(`detail=`/`reason_code=`/예외 생성자/`f"prefix:{...}"` 접두)을 AST로 뽑아 json과 대조하므로, **리터럴을 추가하는 그 태스크에서** json/매핑도 같이 고친다 — 아니면 그 태스크의 전체 스위트가 빨간불이 된다.
- admin 화면은 `<RequireRole role="admin"><AppShell>…</AppShell></RequireRole>` + 사이드바 `{isAdmin && …}`. h1은 정확히 **릴리스**.
- 프론트는 백엔드 응답을 렌더 전에 방어적으로 정규화한다(`Array.isArray`). UI 문자열은 한국어, 로딩 `불러오는 중…`, null은 `—`. 에러 문구는 전부 `reasonText()` 경유.
- 프론트 테스트는 파일마다 자체 MSW `setupServer` + `listen`/`resetHandlers`/`close`, 핸들러 경로는 상대경로.
- **`.venv`에 `kubernetes` 패키지가 없다** (`.venv/bin/python -c "import kubernetes"` → ModuleNotFoundError). `KubernetesClient` 테스트는 `tests/test_k8s_read_pod_log.py`처럼 `_ensure`를 스텁하고 fake를 주입하며, API 예외는 `kubernetes.client.ApiException` import 대신 **`status` 속성을 가진 예외**로 흉내 낸다.
- 백엔드 테스트: `.venv/bin/python -m pytest` (`python`은 PATH에 없다). 전체 스위트는 약 4분 — **포그라운드**로 Bash `timeout` 400000ms. 백그라운드+Monitor 조합 금지.
- 프론트: `cd frontend && npx vitest run`, 타입체크 `npx tsc -b`.
- **origin으로 push 금지.** 커밋만 한다.
- 주석은 한국어로 "왜"를 적는다 (단 `deploy/k8s/10-rbac.yaml`은 기존 영어 주석 톤을 따른다).

## 실측 고정값 (매니페스트·코드에서 확인)

| 항목 | 값 |
|---|---|
| Deployment `dms-api` | 컨테이너 `api`, 이미지 `pkg-01:5000/dms:d22`, 라벨 `app.kubernetes.io/name=dms-api` |
| Deployment `dms-controller` | 컨테이너 `controller`, 이미지 `pkg-01:5000/dms:d22` |
| DaemonSet `dms-agent` | 컨테이너 `agent`, 이미지 `pkg-01:5000/dms-agent:dev5` |
| `releases` 실제 컬럼 | `id`(auto_pk), `component`, `image`, `tag`, `digest`, `state`, `actor`, `applied_at NOT NULL` + 인덱스 `idx_releases_component (component, id)` — `reason_code`/`seq` 없음 |
| `KubernetesClient._ensure` 가드 | `if self._core is None:` — `_core`/`_custom`만 만든다 |
| api 테스트 픽스처 | `tests/conftest.py`의 `client(db, settings)`; admin 헤더는 `{"Authorization": "Bearer tok-shared", "x-dms-actor": "ops"}` (`tests/test_api_builds.py` 패턴) |
| api Role의 pods 동사 | `get, list, watch, delete` (apps 없음 — 설계 §5가 apps는 컨트롤러 Role에만 부여) |

## 파일 구조

| 파일 | 책임 |
|---|---|
| `src/dms/migrations.py` (수정) | `releases`에 `reason_code`, `seq` (CREATE TABLE 텍스트 + `_ensure_columns` 양쪽) |
| `src/dms/repositories/releases.py` (신규) | `ReleasesRepository` + `ROLLOUT_ORDER`/`COMPONENTS` 좌표표. SQL은 전부 여기 |
| `src/dms/repositories/__init__.py` (수정) | `self.releases` 등록 |
| `src/dms/rollout_status.py` (신규) | 정규화(snake/camel)·수렴 판정 **순수 함수**. k8s 접근 없음 |
| `src/dms/rollout_runner.py` (신규) | `WorkloadClient`/`PodBriefReader` Protocol + `RolloutRunner`/`StubRolloutRunner` |
| `src/dms/execution_volcano.py` (수정) | `KubernetesClient`에 `_apps` + `patch_workload`/`get_workload`/`list_pod_briefs` |
| `src/dms/rollout_watcher.py` (신규) | `RolloutWatcher.run_once()` — record-then-patch, 순서 강제, 나이 기반 회수 |
| `src/dms/controller.py` (수정) | `rollout-watcher` 루프 등록 (`rollout_runner=None` 하위호환) |
| `src/dms/cli.py` (수정) | controller 분기에서 `build_rollout_runner` 배선 |
| `src/dms/config.py` (수정) | `rollout_interval_seconds`(10), `rollout_timeout_seconds`(600) |
| `src/dms/wiring.py` (수정) | `build_rollout_runner(settings)` |
| `src/dms/registry.py` (신규) | `fetch_repo_tags` — 레지스트리 v2 태그 조회, 실패 내성 |
| `src/dms/api/routes_releases.py` (신규) | admin 릴리스 API 3종 |
| `src/dms/api/app.py` (수정) | 라우터 등록, `app.state.rollout_runner` |
| `frontend/src/features/releases/*` (신규) | 「릴리스」 화면 + 훅 + 테스트 |
| `frontend/src/app/router.tsx`, `AppShell.tsx` (수정) | 라우트·내비 |
| `frontend/src/lib/types.ts`, `lib/api.ts`, `lib/reasonCodes.json` (수정) | 타입, 사유 코드 (각 백엔드 태스크에서 증분 추가) |
| `deploy/k8s/10-rbac.yaml` (수정) | 컨트롤러 Role에만 apps get/patch/list + `*/status` |
| `deploy/k8s/20-config.yaml`, `deploy/README.md` (수정) | 롤아웃 설정, 수동 매니페스트 동기화 절차 문서 |

---

### Task 1: 스키마 + ReleasesRepository

**Files:**
- Modify: `src/dms/migrations.py`
- Create: `src/dms/repositories/releases.py`
- Modify: `src/dms/repositories/__init__.py`
- Modify: `frontend/src/lib/reasonCodes.json`, `frontend/src/lib/api.ts` (`rollout_in_progress` 1건)
- Test: `tests/test_releases_repo.py`

**Interfaces:**
- Consumes: `Database`(`query`/`query_one`/`execute`/`transaction`), `dump_json`, `utc_now_iso` from `..db`; `DomainValidationError` from `..domain`.
- Produces (뒤 태스크 4·5·6이 이 이름을 그대로 쓴다):
  - `ROLLOUT_ORDER = ("dms-agent", "dms-api", "dms-controller")` — 적용 순서 (컨트롤러가 마지막)
  - `COMPONENTS: dict[str, dict]` — component → `{"kind", "workload", "container", "repository", "selector"}`
  - `ReleasesRepository.create_batch(*, items, actor) -> list[dict]` — `items`는 `[{"component", "image", "tag"}, ...]`. `ROLLOUT_ORDER`로 정렬해 `state='Pending'` 행들을 만들고 생성된 행 dict 목록을 seq 순으로 반환. 활성 배치가 있으면 `DomainValidationError("rollout_in_progress", ...)`
  - `ReleasesRepository.get(release_id) -> dict | None`
  - `ReleasesRepository.list(limit=50) -> list[dict]` — `ORDER BY id DESC` (최신순)
  - `ReleasesRepository.current() -> dict[str, dict]` — 컴포넌트별 `MAX(id)` 행
  - `ReleasesRepository.active() -> list[dict]` — `state IN ('Pending','Applying')` `ORDER BY seq ASC`
  - `ReleasesRepository.mark_applying(release_id) -> None` — `Pending`→`Applying`, `applied_at` 갱신
  - `ReleasesRepository.finish(release_id, *, state, reason_code=None) -> None` — 종단 가드 포함
  - `ReleasesRepository.abort_pending(*, reason_code) -> int` — 남은 `Pending` 전부 `Failed`

행 dict의 키는 컬럼 그대로: `id, component, image, tag, digest, state, actor, applied_at, reason_code, seq`. `applied_at`은 NOT NULL이라 **마지막 전이 시각**으로 쓴다(생성 시각 → Applying 전환 시각 → 종단 시각). `RolloutWatcher`의 나이 기반 회수가 "Applying이 된 시각"으로 이 값을 읽는다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_releases_repo.py`:

```python
import pytest
from dms.db import Database
from dms.domain import DomainValidationError
from dms.migrations import migrate
from dms.repositories import Repositories
from dms.repositories.releases import COMPONENTS, ROLLOUT_ORDER


@pytest.fixture
def repos(tmp_path):
    db = Database.connect(f"sqlite:///{tmp_path}/t.db")
    migrate(db)
    return Repositories(db)


def _items(*components):
    return [{"component": c, "image": f"pkg-01:5000/x:{c}-t1", "tag": f"{c}-t1"}
            for c in components]


def test_rollout_order_is_agent_api_controller():
    # 컨트롤러 자기 갱신이 마지막이어야 앞의 둘이 이미 끝나 있다(설계 §2)
    assert ROLLOUT_ORDER == ("dms-agent", "dms-api", "dms-controller")


def test_components_carry_real_container_names():
    # 컨테이너 이름은 워크로드 이름에서 유도되지 않는다(dms-controller → controller)
    assert COMPONENTS["dms-agent"] == {
        "kind": "DaemonSet", "workload": "dms-agent", "container": "agent",
        "repository": "dms-agent", "selector": "app.kubernetes.io/name=dms-agent"}
    assert COMPONENTS["dms-api"]["container"] == "api"
    assert COMPONENTS["dms-api"]["repository"] == "dms"
    assert COMPONENTS["dms-controller"]["container"] == "controller"
    assert COMPONENTS["dms-controller"]["kind"] == "Deployment"


def test_create_batch_persists_rollout_order(repos):
    # 제출 순서가 아니라 ROLLOUT_ORDER가 seq를 결정한다 — 배치 중간에 죽은
    # 컨트롤러가 DB의 seq만 보고 이어가야 하므로 순서는 반드시 지속돼야 한다
    rows = repos.releases.create_batch(
        items=_items("dms-controller", "dms-agent"), actor="ops")
    assert [r["component"] for r in rows] == ["dms-agent", "dms-controller"]
    assert rows[0]["seq"] < rows[1]["seq"]
    assert all(r["state"] == "Pending" for r in rows)


def test_create_batch_rejects_concurrent_rollout(repos):
    repos.releases.create_batch(items=_items("dms-api"), actor="ops")
    with pytest.raises(DomainValidationError) as e:
        repos.releases.create_batch(items=_items("dms-agent"), actor="ops")
    assert e.value.reason_code == "rollout_in_progress"


def test_create_batch_writes_release_audit(repos):
    repos.releases.create_batch(items=_items("dms-api"), actor="ops")
    entries = repos.control.audit_entries(limit=5)
    assert any(e["mutation_class"] == "release" and e["actor"] == "ops"
               for e in entries)


def test_active_and_transitions(repos):
    rows = repos.releases.create_batch(items=_items("dms-agent", "dms-api"),
                                       actor="ops")
    head = rows[0]
    repos.releases.mark_applying(head["id"])
    active = repos.releases.active()
    assert [r["state"] for r in active] == ["Applying", "Pending"]
    repos.releases.finish(head["id"], state="Applied")
    assert [r["id"] for r in repos.releases.active()] == [rows[1]["id"]]


def test_mark_applying_updates_applied_at(repos):
    row = repos.releases.create_batch(items=_items("dms-api"), actor="ops")[0]
    repos.releases.mark_applying(row["id"])
    after = repos.releases.get(row["id"])
    assert after["state"] == "Applying"
    assert after["applied_at"] >= row["applied_at"]


def test_finish_is_terminal_guarded(repos):
    row = repos.releases.create_batch(items=_items("dms-api"), actor="ops")[0]
    repos.releases.mark_applying(row["id"])
    repos.releases.finish(row["id"], state="Applied")
    repos.releases.finish(row["id"], state="Failed", reason_code="rollout_timeout")
    assert repos.releases.get(row["id"])["state"] == "Applied"   # 종단은 못 덮는다


def test_abort_pending_only_touches_pending(repos):
    rows = repos.releases.create_batch(items=_items("dms-agent", "dms-api"),
                                       actor="ops")
    repos.releases.mark_applying(rows[0]["id"])
    repos.releases.finish(rows[0]["id"], state="Failed", reason_code="rollout_timeout")
    n = repos.releases.abort_pending(reason_code="rollout_aborted")
    assert n == 1
    assert repos.releases.get(rows[0]["id"])["reason_code"] == "rollout_timeout"
    tail = repos.releases.get(rows[1]["id"])
    assert (tail["state"], tail["reason_code"]) == ("Failed", "rollout_aborted")


def test_current_is_max_id_per_component(repos):
    a = repos.releases.create_batch(items=_items("dms-api"), actor="ops")[0]
    repos.releases.mark_applying(a["id"])
    repos.releases.finish(a["id"], state="Applied")
    b = repos.releases.create_batch(items=_items("dms-api"), actor="ops")[0]
    current = repos.releases.current()
    assert current["dms-api"]["id"] == b["id"]      # 상태 무관, MAX(id)가 "현재"


def test_list_is_newest_first(repos):
    a = repos.releases.create_batch(items=_items("dms-api"), actor="ops")[0]
    repos.releases.finish(a["id"], state="Failed", reason_code="rollout_timeout")
    b = repos.releases.create_batch(items=_items("dms-agent"), actor="ops")[0]
    ids = [r["id"] for r in repos.releases.list(limit=10)]
    assert ids.index(b["id"]) < ids.index(a["id"])
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_releases_repo.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'dms.repositories.releases'`

- [ ] **Step 3: 마이그레이션에 컬럼을 더한다**

`src/dms/migrations.py`의 `releases` CREATE TABLE 텍스트를 다음으로 바꾼다 (builds의 seq 주석과 같은 이유 — SQLite ALTER는 UNIQUE/NOT NULL을 못 붙이므로 제약은 애플리케이션에 둔다):

```python
        f"""CREATE TABLE IF NOT EXISTS releases (
            id {auto_pk},
            component TEXT NOT NULL,
            image TEXT NOT NULL,
            tag TEXT NOT NULL,
            digest TEXT,
            state TEXT NOT NULL,
            reason_code TEXT,
            -- seq: 배치 안 적용 순서(전역 단조 증가). builds.seq와 같은 이유로
            -- 제약은 여기 걸지 않고 create_batch()의 MAX(seq)+1이 지킨다.
            seq INTEGER,
            actor TEXT NOT NULL,
            applied_at TEXT NOT NULL)""",
```

`_ensure_columns`의 튜플에 두 줄을 더한다:

```python
        ("releases", "reason_code", "TEXT"),
        ("releases", "seq", "INTEGER"),
```

- [ ] **Step 4: ReleasesRepository를 만든다**

`src/dms/repositories/releases.py`:

```python
from ..db import Database, dump_json, utc_now_iso
from ..domain import DomainValidationError

# 적용 순서. 컨트롤러가 자기 Deployment를 패치하면 롤아웃을 수행하던 파드 자신이
# 죽는다 -- 마지막에 둬야 앞의 둘이 이미 종단이고, 새 컨트롤러 파드는 자기 행 하나만
# 이어받으면 된다(설계 §2).
ROLLOUT_ORDER = ("dms-agent", "dms-api", "dms-controller")

# component -> 워크로드 좌표. 컨테이너 이름은 워크로드 이름에서 유도되지 않는다
# (실측: dms-controller의 컨테이너는 "controller", dms-api는 "api") -- 표로 박아둔다.
# repository는 레지스트리 리포 이름: api/controller는 같은 dms 이미지 계보를 쓴다.
COMPONENTS = {
    "dms-agent": {"kind": "DaemonSet", "workload": "dms-agent",
                  "container": "agent", "repository": "dms-agent",
                  "selector": "app.kubernetes.io/name=dms-agent"},
    "dms-api": {"kind": "Deployment", "workload": "dms-api",
                "container": "api", "repository": "dms",
                "selector": "app.kubernetes.io/name=dms-api"},
    "dms-controller": {"kind": "Deployment", "workload": "dms-controller",
                       "container": "controller", "repository": "dms",
                       "selector": "app.kubernetes.io/name=dms-controller"},
}

_ACTIVE = ("Pending", "Applying")
_TERMINAL = ("Applied", "Failed")


class ReleasesRepository:
    def __init__(self, db: Database):
        self._db = db

    def _audit(self, operation, target, before, after, actor):
        self._db.execute(
            """INSERT INTO audit_log (mutation_class, operation, target_key, actor,
                   before_state, after_state, at)
               VALUES ('release', :op, :key, :actor, :b, :a, :at)""",
            {"op": operation, "key": target, "actor": actor,
             "b": dump_json(before) if before is not None else None,
             "a": dump_json(after) if after is not None else None,
             "at": utc_now_iso()})

    def create_batch(self, *, items, actor) -> list[dict]:
        # 순서를 DB에 지속시킨다: 제출 순서가 아니라 ROLLOUT_ORDER가 seq를 정한다.
        # 배치 중간에 죽은 컨트롤러는 seq만 보고 이어가므로, 순서가 행에 없으면
        # 이미 끝낸 패치를 다시 하거나 컨트롤러를 먼저 죽이는 사고가 난다(설계 §2).
        ordered = sorted(items, key=lambda i: ROLLOUT_ORDER.index(i["component"]))
        now = utc_now_iso()
        with self._db.transaction():
            # "동시 롤아웃 1개"의 진짜 가드 -- builds.create()와 같은 관용구로
            # 존재 확인과 INSERT를 같은 트랜잭션 안에서 원자적으로 처리한다.
            if self.active():
                raise DomainValidationError(
                    "rollout_in_progress", "an active rollout already exists")
            row = self._db.query_one("SELECT COALESCE(MAX(seq), 0) AS m FROM releases")
            seq = row["m"]
            first_seq = seq + 1
            for item in ordered:
                seq += 1
                self._db.execute(
                    """INSERT INTO releases (component, image, tag, digest, state,
                           reason_code, seq, actor, applied_at)
                       VALUES (:c, :img, :tag, NULL, 'Pending', NULL, :seq, :actor, :now)""",
                    {"c": item["component"], "img": item["image"],
                     "tag": item["tag"], "seq": seq, "actor": actor, "now": now})
            self._audit("create", f"seq:{first_seq}-{seq}", None,
                        {"items": ordered}, actor)
            rows = self._db.query(
                "SELECT * FROM releases WHERE seq >= :s ORDER BY seq ASC",
                {"s": first_seq})
        return [dict(r) for r in rows]

    def get(self, release_id):
        row = self._db.query_one("SELECT * FROM releases WHERE id = :id",
                                 {"id": release_id})
        return dict(row) if row else None

    def list(self, limit: int = 50):
        rows = self._db.query(
            "SELECT * FROM releases ORDER BY id DESC LIMIT :n", {"n": limit})
        return [dict(r) for r in rows]

    def current(self):
        # "현재 릴리스"는 컴포넌트별 MAX(id)로 유도한다 -- component 유니크 제약이
        # 없고 인덱스가 (component, id)다(설계 §6).
        rows = self._db.query(
            """SELECT r.* FROM releases r
               JOIN (SELECT component, MAX(id) AS mid FROM releases
                     GROUP BY component) m ON r.id = m.mid""")
        return {r["component"]: dict(r) for r in rows}

    def active(self):
        rows = self._db.query(
            """SELECT * FROM releases WHERE state IN ('Pending', 'Applying')
               ORDER BY seq ASC""")
        return [dict(r) for r in rows]

    def mark_applying(self, release_id) -> None:
        # applied_at을 갱신해 "Applying이 된 시각"을 남긴다 -- RolloutWatcher의
        # 나이 기반 회수(벽시계 타임아웃)가 이 값을 기준으로 잰다.
        self._db.execute(
            """UPDATE releases SET state = 'Applying', applied_at = :now
               WHERE id = :id AND state = 'Pending'""",
            {"now": utc_now_iso(), "id": release_id})

    def finish(self, release_id, *, state, reason_code=None) -> None:
        self._db.execute(
            """UPDATE releases SET state = :st, reason_code = :rc, applied_at = :now
               WHERE id = :id AND state NOT IN ('Applied', 'Failed')""",
            {"st": state, "rc": reason_code, "now": utc_now_iso(),
             "id": release_id})

    def abort_pending(self, *, reason_code) -> int:
        # 한 컴포넌트가 Failed면 뒤 Pending들을 종단시켜 배치를 닫는다 -- 안 하면
        # active()가 비지 않아 rollout_in_progress가 영원히 새 롤아웃을 막는다.
        rows = self._db.query(
            "SELECT id FROM releases WHERE state = 'Pending' ORDER BY seq ASC")
        for row in rows:
            self.finish(row["id"], state="Failed", reason_code=reason_code)
        return len(rows)
```

- [ ] **Step 5: Repositories에 등록한다**

`src/dms/repositories/__init__.py`에 `from .releases import ReleasesRepository`와 `self.releases = ReleasesRepository(db)`를 더한다.

- [ ] **Step 6: 사유 코드 목록을 갱신한다**

이 태스크가 백엔드 리터럴 `rollout_in_progress`(DomainValidationError 생성자 — AST 추출 대상)를 추가했다. `frontend/src/lib/reasonCodes.json`의 builds 코드 그룹 뒤에 `"rollout_in_progress"`를 넣고, `frontend/src/lib/api.ts`의 `REASON_MESSAGES`에 다음을 더한다:

```ts
  rollout_in_progress: "이미 진행 중인 롤아웃이 있습니다",
```

- [ ] **Step 7: 테스트를 통과시킨다**

Run: `.venv/bin/python -m pytest tests/test_releases_repo.py tests/test_migrations.py -q`
Expected: PASS (신규 11 tests + 기존 마이그레이션 테스트)

Run: `cd frontend && npx vitest run src/lib/reasonCodes.test.ts`
Expected: PASS (커버리지·죽은 키 둘 다)

- [ ] **Step 8: 전체 스위트로 회귀를 확인한다**

Run: `.venv/bin/python -m pytest -q` (포그라운드, timeout 400000ms)
Expected: 전부 PASS — 특히 `tests/test_reason_codes_coverage.py`

- [ ] **Step 9: 커밋**

```bash
git add src/dms/migrations.py src/dms/repositories/releases.py src/dms/repositories/__init__.py frontend/src/lib/reasonCodes.json frontend/src/lib/api.ts tests/test_releases_repo.py
git commit -m "feat(releases): releases 저장소와 reason_code/seq 마이그레이션"
```

---
### Task 2: 수렴 판정 순수 함수 (rollout_status)

**Files:**
- Create: `src/dms/rollout_status.py`
- Test: `tests/test_rollout_status.py`

**Interfaces:**
- Consumes: 없음 (표준 라이브러리만 — k8s 클라이언트 접근 금지).
- Produces (Task 3의 클라이언트와 Task 4의 워처가 그대로 쓴다):
  - `normalize_deployment(obj: dict) -> dict` — snake_case(`to_dict()`)든 camelCase(원시 dict)든 같은 정규화 dict를 낸다. 키: `kind`("Deployment"), `generation: int`, `observed_generation: int`(없으면 **-1**), `replicas: int`(spec, 없으면 1), `status_replicas: int`, `updated_replicas: int`, `ready_replicas: int`(각 없으면 0), `conditions: list[dict]`(`type`/`status`/`reason`/`message`), `images: dict[str, str]`(spec.template의 컨테이너명→이미지)
  - `normalize_daemonset(obj: dict) -> dict` — 키: `kind`("DaemonSet"), `generation`, `observed_generation`(없으면 -1), `desired_number_scheduled`, `updated_number_scheduled`, `number_ready`, `number_unavailable`(unset→0), `number_misscheduled`(각 없으면 0), `images`
  - `assess_deployment(norm: dict) -> tuple[str, str | None]` — `("applied" | "progressing" | "failed", detail)`
  - `assess_daemonset(norm: dict) -> tuple[str, str | None]` — `("applied" | "progressing", detail)` (DaemonSet에는 실패 조건이 없다 — 실패는 워처의 벽시계가 정한다)

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_rollout_status.py`:

```python
import pytest
from dms.rollout_status import (assess_daemonset, assess_deployment,
                                normalize_daemonset, normalize_deployment)

# 같은 Deployment를 두 표기로 만든다 -- to_dict()는 snake_case, 원시 dict는
# camelCase. 정규화가 이 차이를 흡수하지 못하면 페어는 통과하고 프로덕션만
# None을 읽어 "영원히 수렴 안 함"이 된다(설계 §4).
DEPLOY_SNAKE = {
    "metadata": {"generation": 3},
    "spec": {"replicas": 2, "template": {"spec": {"containers": [
        {"name": "api", "image": "pkg-01:5000/dms:d23"}]}}},
    "status": {"observed_generation": 3, "replicas": 2, "updated_replicas": 2,
               "ready_replicas": 2,
               "conditions": [{"type": "Progressing", "status": "True",
                               "reason": "NewReplicaSetAvailable", "message": "ok"}]},
}
DEPLOY_CAMEL = {
    "metadata": {"generation": 3},
    "spec": {"replicas": 2, "template": {"spec": {"containers": [
        {"name": "api", "image": "pkg-01:5000/dms:d23"}]}}},
    "status": {"observedGeneration": 3, "replicas": 2, "updatedReplicas": 2,
               "readyReplicas": 2,
               "conditions": [{"type": "Progressing", "status": "True",
                               "reason": "NewReplicaSetAvailable", "message": "ok"}]},
}


@pytest.mark.parametrize("obj", [DEPLOY_SNAKE, DEPLOY_CAMEL])
def test_both_notations_normalize_identically(obj):
    norm = normalize_deployment(obj)
    assert norm == normalize_deployment(DEPLOY_SNAKE)
    assert norm["observed_generation"] == 3
    assert norm["images"] == {"api": "pkg-01:5000/dms:d23"}


def test_converged_deployment_is_applied():
    assert assess_deployment(normalize_deployment(DEPLOY_SNAKE)) == ("applied", None)


def test_generation_gate_blocks_stale_success():
    # 패치 직후: 상태 필드는 전부 패치 이전(수렴한 옛 ReplicaSet) 값이다.
    # 게이트가 없으면 여기서 "applied"가 나온다 -- 전형적인 거짓 성공(설계 §3).
    stale = {**DEPLOY_SNAKE, "metadata": {"generation": 4}}
    assert assess_deployment(normalize_deployment(stale)) == ("progressing", None)


def test_progress_deadline_exceeded_is_failed():
    obj = {**DEPLOY_SNAKE, "status": {
        "observed_generation": 3, "replicas": 2, "updated_replicas": 1,
        "ready_replicas": 1,
        "conditions": [{"type": "Progressing", "status": "False",
                        "reason": "ProgressDeadlineExceeded", "message": "exceeded"}]}}
    verdict, detail = assess_deployment(normalize_deployment(obj))
    assert verdict == "failed"
    assert "ProgressDeadlineExceeded" in detail


def test_replica_failure_condition_surfaces_as_detail():
    # /cephfs hostPath type:Directory가 없는 노드의 admission 오류가 여기 실린다
    obj = {**DEPLOY_SNAKE, "status": {
        "observed_generation": 3, "replicas": 2, "updated_replicas": 1,
        "ready_replicas": 1,
        "conditions": [{"type": "ReplicaFailure", "status": "True",
                        "reason": "FailedCreate", "message": "hostPath missing"}]}}
    verdict, detail = assess_deployment(normalize_deployment(obj))
    assert verdict == "progressing"
    assert "hostPath missing" in detail


def test_old_pods_still_around_is_progressing():
    # updated == desired 여도 status.replicas > updated면 옛 파드가 남아 있다
    obj = {**DEPLOY_SNAKE, "status": {
        "observed_generation": 3, "replicas": 3, "updated_replicas": 2,
        "ready_replicas": 2, "conditions": []}}
    assert assess_deployment(normalize_deployment(obj))[0] == "progressing"


def test_missing_observed_generation_never_converges():
    obj = {"metadata": {"generation": 1}, "spec": {"replicas": 1},
           "status": {"replicas": 1, "updated_replicas": 1, "ready_replicas": 1}}
    norm = normalize_deployment(obj)
    assert norm["observed_generation"] == -1
    assert assess_deployment(norm)[0] == "progressing"


DS_CAMEL = {
    "metadata": {"generation": 5},
    "spec": {"template": {"spec": {"containers": [
        {"name": "agent", "image": "pkg-01:5000/dms-agent:dev6"}]}}},
    "status": {"observedGeneration": 5, "desiredNumberScheduled": 5,
               "updatedNumberScheduled": 5, "numberReady": 5,
               "numberMisscheduled": 0},
}


def test_daemonset_unset_unavailable_counts_as_zero():
    # numberUnavailable은 0이면 아예 빠진다 -- unset을 0으로 안 읽으면 영원히 progressing
    norm = normalize_daemonset(DS_CAMEL)
    assert norm["number_unavailable"] == 0
    assert assess_daemonset(norm) == ("applied", None)


def test_daemonset_generation_gate_applies():
    stale = {**DS_CAMEL, "metadata": {"generation": 6}}
    assert assess_daemonset(normalize_daemonset(stale))[0] == "progressing"


@pytest.mark.parametrize("patch,expected", [
    ({"updatedNumberScheduled": 4}, "progressing"),   # 아직 옛 파드가 있는 노드
    ({"numberReady": 4}, "progressing"),              # 새 파드가 Ready가 아님
    ({"numberUnavailable": 1}, "progressing"),
    ({"numberMisscheduled": 1}, "progressing"),
])
def test_daemonset_each_gate_blocks_applied(patch, expected):
    obj = {**DS_CAMEL, "status": {**DS_CAMEL["status"], **patch}}
    assert assess_daemonset(normalize_daemonset(obj))[0] == expected


def test_daemonset_snake_case_status_normalizes_too():
    snake = {"metadata": {"generation": 5},
             "spec": {"template": {"spec": {"containers": [
                 {"name": "agent", "image": "i"}]}}},
             "status": {"observed_generation": 5, "desired_number_scheduled": 5,
                        "updated_number_scheduled": 5, "number_ready": 5,
                        "number_misscheduled": 0}}
    assert assess_daemonset(normalize_daemonset(snake)) == ("applied", None)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_rollout_status.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'dms.rollout_status'`

- [ ] **Step 3: 구현한다**

`src/dms/rollout_status.py`:

```python
"""워크로드 상태 정규화와 수렴 판정. 순수 함수 -- k8s 클라이언트에 접근하지 않는다.

kubernetes 파이썬 클라이언트의 to_dict()는 snake_case 키를, (테스트 fake나 원시
CRD처럼) dict 그대로 온 객체는 camelCase 키를 준다. 여기서 한 표기로 정규화하지
않으면 테스트 페어는 통과하고 프로덕션만 None을 읽어 "영원히 수렴 안 함"으로
보고한다(설계 §4). 판정 함수는 정규화된 dict만 받는다."""


def _num(mapping, snake, camel, default=0):
    value = mapping.get(snake, mapping.get(camel))
    return default if value is None else int(value)


def _images(obj):
    spec = obj.get("spec") or {}
    template = spec.get("template") or {}
    pod_spec = template.get("spec") or {}
    return {c.get("name"): c.get("image")
            for c in (pod_spec.get("containers") or []) if c.get("name")}


def _generations(obj):
    meta = obj.get("metadata") or {}
    status = obj.get("status") or {}
    generation = int(meta.get("generation") or 0)
    # observedGeneration 부재는 -1 -- 0으로 두면 generation도 0인 비정상 객체에서
    # 게이트(observed >= generation)가 통과해 버린다. 모르면 수렴 아님이 안전하다.
    observed = _num(status, "observed_generation", "observedGeneration", default=-1)
    return generation, observed


def _conditions(status):
    return [{"type": c.get("type"), "status": c.get("status"),
             "reason": c.get("reason"), "message": c.get("message")}
            for c in (status.get("conditions") or [])]


def normalize_deployment(obj: dict) -> dict:
    status = obj.get("status") or {}
    spec = obj.get("spec") or {}
    generation, observed = _generations(obj)
    replicas = spec.get("replicas")
    return {
        "kind": "Deployment",
        "generation": generation,
        "observed_generation": observed,
        "replicas": 1 if replicas is None else int(replicas),  # k8s 기본값 1
        "status_replicas": _num(status, "replicas", "replicas"),
        "updated_replicas": _num(status, "updated_replicas", "updatedReplicas"),
        "ready_replicas": _num(status, "ready_replicas", "readyReplicas"),
        "conditions": _conditions(status),
        "images": _images(obj),
    }


def normalize_daemonset(obj: dict) -> dict:
    status = obj.get("status") or {}
    generation, observed = _generations(obj)
    return {
        "kind": "DaemonSet",
        "generation": generation,
        "observed_generation": observed,
        "desired_number_scheduled": _num(status, "desired_number_scheduled",
                                         "desiredNumberScheduled"),
        "updated_number_scheduled": _num(status, "updated_number_scheduled",
                                         "updatedNumberScheduled"),
        "number_ready": _num(status, "number_ready", "numberReady"),
        # 0이면 필드 자체가 빠진다 -- unset을 0으로 읽지 않으면 영원히 progressing
        "number_unavailable": _num(status, "number_unavailable", "numberUnavailable"),
        "number_misscheduled": _num(status, "number_misscheduled",
                                    "numberMisscheduled"),
        "images": _images(obj),
    }


def assess_deployment(norm: dict) -> "tuple[str, str | None]":
    """("applied" | "progressing" | "failed", detail).

    세대 게이트를 반드시 먼저 본다 -- 통과 전의 상태 필드는 전부 패치 이전 값이라
    옛 ReplicaSet 기준 거짓 성공이 난다(설계 §3)."""
    if norm["observed_generation"] < norm["generation"]:
        return ("progressing", None)
    for cond in norm["conditions"]:
        if (cond.get("type") == "Progressing" and cond.get("status") == "False"
                and cond.get("reason") == "ProgressDeadlineExceeded"):
            # progressDeadlineSeconds=600이 이미 설정돼 있어 10분 상한을 공짜로
            # 물려받는다 -- 자체 상한을 더 두지 않는다(설계 §3).
            return ("failed", f"ProgressDeadlineExceeded: {cond.get('message') or ''}"[:200])
    if (norm["updated_replicas"] == norm["replicas"]
            and norm["status_replicas"] == norm["updated_replicas"]
            and norm["ready_replicas"] == norm["updated_replicas"]):
        return ("applied", None)
    for cond in norm["conditions"]:
        if cond.get("type") == "ReplicaFailure" and cond.get("status") == "True":
            # 종단이 아니라 노출이다 -- admission 오류(/cephfs hostPath 없음 등)를
            # 운영자가 실패 확정 전에 볼 수 있어야 한다. 확정은 PDE가 한다.
            return ("progressing", (cond.get("message") or cond.get("reason") or "")[:200])
    return ("progressing", None)


def assess_daemonset(norm: dict) -> "tuple[str, str | None]":
    """("applied" | "progressing", detail). DaemonSet에는 conditions도
    progressDeadlineSeconds도 없다(설계 §3) -- 실패 확정은 여기서 하지 않고
    RolloutWatcher의 벽시계 타임아웃이 한다."""
    if norm["observed_generation"] < norm["generation"]:
        return ("progressing", None)
    desired = norm["desired_number_scheduled"]
    if (norm["updated_number_scheduled"] == desired
            and norm["number_ready"] == desired
            and norm["number_unavailable"] == 0
            and norm["number_misscheduled"] == 0):
        return ("applied", None)
    return ("progressing", None)
```

- [ ] **Step 4: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_rollout_status.py -q`
Expected: PASS (15 tests — parametrize 포함)

- [ ] **Step 5: 커밋**

```bash
git add src/dms/rollout_status.py tests/test_rollout_status.py
git commit -m "feat(releases): 워크로드 상태 정규화와 수렴 판정 순수 함수"
```

---
### Task 3: WorkloadClient + KubernetesClient 확장 + RolloutRunner + wiring

**Files:**
- Create: `src/dms/rollout_runner.py`
- Modify: `src/dms/execution_volcano.py` (`KubernetesClient`만 — `K8sClient` Protocol과 `VolcanoExecutionAdapter`는 손대지 않는다)
- Modify: `src/dms/wiring.py`, `src/dms/config.py`
- Modify: `frontend/src/lib/reasonCodes.json`, `frontend/src/lib/api.ts` (`patch_failed`, `observe_failed`)
- Test: `tests/test_rollout_runner.py`

**Interfaces:**
- Consumes: `ExecutionError` from `.execution`; `normalize_deployment`/`normalize_daemonset` from `.rollout_status` (Task 2).
- Produces (Task 4·5가 그대로 쓴다):
  - `WorkloadClient` Protocol: `patch_workload(kind, name, namespace, body) -> None`, `get_workload(kind, name, namespace) -> dict | None` (**정규화 dict** 반환 — 정규화는 클라이언트 안)
  - `PodBriefReader` Protocol: `list_pod_briefs(namespace, label_selector) -> list[dict]` — 항목 `{"name", "node", "images": {컨테이너: 이미지}, "phase", "waiting_reason": str | None}`
  - `image_patch_body(container: str, image: str) -> dict` — strategic merge patch 본문 (순수 함수)
  - `RolloutRunner(k8s, *, namespace)`:
    - `.patch_image(*, kind, name, container, image) -> None` — 실패 시 `ExecutionError("patch_failed", ...)`
    - `.observe(*, kind, name) -> dict | None` — 404는 `None`, 그 외 `ExecutionError("observe_failed", ...)`
    - `.pod_briefs(*, selector) -> list[dict]` — **best-effort**, 실패 시 빈 리스트 (진단·현재 이미지 조회용)
  - `StubRolloutRunner` — 클러스터 없이 도는 결정적 페어. `.patched: list[tuple]` 기록, `observe`는 패치한 이미지로 즉시 수렴한 정규화 dict
  - `build_rollout_runner(settings) -> RolloutRunner | StubRolloutRunner` in `wiring.py`
  - `Settings.rollout_interval_seconds: int = 10`, `Settings.rollout_timeout_seconds: int = 600`

**왜 `PodBriefReader`가 따로 있나:** (1) DaemonSet 타임아웃 시 "멈춘 노드의 파드 사유"를 보고해야 하고(설계 §3), (2) api 파드는 apps RBAC이 없으므로(설계 §5는 apps를 **컨트롤러 Role에만** 부여) targets 화면의 "클러스터의 현재 이미지"(설계 §7)를 이미 가진 `pods list` 권한으로 읽어야 한다. `WorkloadClient`는 설계 §4대로 두 메서드로 유지하고, 파드 읽기는 별도 좁은 Protocol로 둔다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_rollout_runner.py`:

```python
import pytest
from dms.execution import ExecutionError
from dms.execution_volcano import KubernetesClient
from dms.rollout_runner import RolloutRunner, StubRolloutRunner, image_patch_body


def test_patch_body_is_strategic_merge_shape():
    # 컨테이너 name이 patchMergeKey다 -- 이 모양이 아니면 JSON merge처럼
    # containers 배열 전체가 교체돼 env/volumeMounts가 날아간다(설계 §4)
    assert image_patch_body("api", "pkg-01:5000/dms:d23") == {
        "spec": {"template": {"spec": {"containers": [
            {"name": "api", "image": "pkg-01:5000/dms:d23"}]}}}}


class _FakeWorkloads:
    """WorkloadClient 페어 -- get_workload는 계약대로 '정규화된' dict를 돌려준다."""
    def __init__(self):
        self.patched = []
        self.objects = {}
        self.fail_patch = None
        self.fail_get = None

    def patch_workload(self, kind, name, namespace, body):
        if self.fail_patch:
            raise self.fail_patch
        self.patched.append((kind, name, namespace, body))

    def get_workload(self, kind, name, namespace):
        if self.fail_get:
            raise self.fail_get
        return self.objects.get((kind, name))

    def list_pod_briefs(self, namespace, label_selector):
        return [{"name": "p1", "node": "dms-w1", "images": {"api": "i1"},
                 "phase": "Running", "waiting_reason": None}]


def test_runner_patches_via_client():
    k8s = _FakeWorkloads()
    RolloutRunner(k8s, namespace="dms").patch_image(
        kind="Deployment", name="dms-api", container="api", image="img:t")
    kind, name, ns, body = k8s.patched[0]
    assert (kind, name, ns) == ("Deployment", "dms-api", "dms")
    assert body == image_patch_body("api", "img:t")


def test_patch_failure_becomes_execution_error():
    k8s = _FakeWorkloads()
    k8s.fail_patch = RuntimeError("boom")
    with pytest.raises(ExecutionError) as e:
        RolloutRunner(k8s, namespace="dms").patch_image(
            kind="Deployment", name="dms-api", container="api", image="i")
    assert e.value.reason_code == "patch_failed"


def test_observe_passes_through_normalized_dict_and_none():
    k8s = _FakeWorkloads()
    k8s.objects[("DaemonSet", "dms-agent")] = {"kind": "DaemonSet", "generation": 1}
    r = RolloutRunner(k8s, namespace="dms")
    assert r.observe(kind="DaemonSet", name="dms-agent")["generation"] == 1
    assert r.observe(kind="DaemonSet", name="gone") is None


def test_observe_failure_becomes_execution_error():
    k8s = _FakeWorkloads()
    k8s.fail_get = RuntimeError("apiserver down")
    with pytest.raises(ExecutionError) as e:
        RolloutRunner(k8s, namespace="dms").observe(kind="Deployment", name="dms-api")
    assert e.value.reason_code == "observe_failed"


def test_pod_briefs_is_best_effort():
    class _Boom(_FakeWorkloads):
        def list_pod_briefs(self, namespace, label_selector):
            raise RuntimeError("nope")
    r = RolloutRunner(_Boom(), namespace="dms")
    assert r.pod_briefs(selector="app.kubernetes.io/name=dms-api") == []
    ok = RolloutRunner(_FakeWorkloads(), namespace="dms")
    assert ok.pod_briefs(selector="x")[0]["images"] == {"api": "i1"}


def test_stub_runner_converges_on_patched_image():
    stub = StubRolloutRunner()
    stub.patch_image(kind="Deployment", name="dms-api", container="api", image="i2")
    obs = stub.observe(kind="Deployment", name="dms-api")
    assert obs["images"] == {"api": "i2"}
    assert obs["observed_generation"] >= obs["generation"]
    assert stub.observe(kind="Deployment", name="never-patched") is None
    assert stub.patched == [("Deployment", "dms-api", "api", "i2")]
    assert stub.pod_briefs(selector="x") == []


# ---- KubernetesClient 확장: _ensure를 스텁하고 fake apps/core를 주입한다
#      (tests/test_k8s_read_pod_log.py와 같은 패턴 -- .venv에 kubernetes가 없다) ----

class _ApiError(Exception):
    """kubernetes.client.ApiException 흉내 -- 클라이언트는 status 속성만 본다."""
    def __init__(self, status):
        self.status = status
        super().__init__(f"status={status}")


class _Obj:
    def __init__(self, payload):
        self._payload = payload

    def to_dict(self):
        return self._payload


class _FakeApps:
    def __init__(self):
        self.calls = []
        self.raise_on_read = None
        self.deployment = {
            "metadata": {"generation": 1},
            "spec": {"replicas": 1, "template": {"spec": {"containers": [
                {"name": "api", "image": "i"}]}}},
            "status": {"observed_generation": 1, "replicas": 1,
                       "updated_replicas": 1, "ready_replicas": 1,
                       "conditions": []}}

    def patch_namespaced_deployment(self, name, namespace, body, **kwargs):
        self.calls.append(("patch_deploy", name, namespace, body, kwargs))

    def patch_namespaced_daemon_set(self, name, namespace, body, **kwargs):
        self.calls.append(("patch_ds", name, namespace, body, kwargs))

    def read_namespaced_deployment(self, name, namespace):
        if self.raise_on_read:
            raise self.raise_on_read
        return _Obj(self.deployment)

    def read_namespaced_daemon_set(self, name, namespace):
        if self.raise_on_read:
            raise self.raise_on_read
        return _Obj({"metadata": {"generation": 1}, "status": {}})


def _client(apps):
    c = KubernetesClient("dms")
    c._ensure = lambda: None          # in-cluster config 로드를 건너뛴다
    c._apps = apps
    return c


def test_patch_workload_sends_explicit_strategic_content_type():
    apps = _FakeApps()
    _client(apps).patch_workload("Deployment", "dms-api", "dms",
                                 image_patch_body("api", "i2"))
    op, name, ns, body, kwargs = apps.calls[0]
    assert (op, name, ns) == ("patch_deploy", "dms-api", "dms")
    # 클라이언트 내부 기본값에 기대지 않는다 -- 명시하지 않으면 배열 교체 사고가 난다
    assert kwargs["_content_type"] == "application/strategic-merge-patch+json"


def test_patch_workload_routes_daemonset_to_daemonset_api():
    apps = _FakeApps()
    _client(apps).patch_workload("DaemonSet", "dms-agent", "dms", {})
    assert apps.calls[0][0] == "patch_ds"


def test_get_workload_normalizes_to_dict_payload():
    got = _client(_FakeApps()).get_workload("Deployment", "dms-api", "dms")
    # snake_case to_dict() 페이로드가 정규화 키로 돌아온다
    assert got["observed_generation"] == 1 and got["images"] == {"api": "i"}


def test_get_workload_404_is_none_and_403_reraises(caplog):
    apps = _FakeApps()
    apps.raise_on_read = _ApiError(404)
    assert _client(apps).get_workload("Deployment", "dms-api", "dms") is None
    apps.raise_on_read = _ApiError(403)
    with caplog.at_level("ERROR"):
        with pytest.raises(_ApiError):
            _client(apps).get_workload("Deployment", "dms-api", "dms")
    # RBAC 거부가 "없다"와 똑같이 보이면 안 된다 -- 로그로 구분(설계 §4)
    assert "403" in caplog.text


def test_unsupported_kind_is_rejected():
    with pytest.raises(ValueError):
        _client(_FakeApps()).patch_workload("StatefulSet", "x", "dms", {})
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_rollout_runner.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'dms.rollout_runner'`

- [ ] **Step 3: rollout_runner 모듈을 만든다**

`src/dms/rollout_runner.py`:

```python
"""롤아웃의 k8s I/O. 기존 K8sClient(create/get/delete/read_pod_log)를 확장하지
않는다 -- 네 개의 기존 테스트 페어가 그 계약을 구조적으로 구현하고 있고, 그중
apps/v1 동사가 필요한 것은 하나도 없다(설계 §4). 좁은 Protocol을 여기 따로 두고,
같은 구체 클래스 KubernetesClient가 구조적 타이핑으로 둘 다 만족한다 --
BuildRunner가 러너 수준에서 분리하되 클라이언트는 공유한 것과 같은 방식이다."""
import logging
from typing import Protocol

from .execution import ExecutionError

logger = logging.getLogger(__name__)


class WorkloadClient(Protocol):
    def patch_workload(self, kind: str, name: str, namespace: str,
                       body: dict) -> None: ...
    def get_workload(self, kind: str, name: str, namespace: str) -> "dict | None":
        """정규화된 상태 dict(rollout_status.normalize_*) 또는 404면 None."""
        ...


class PodBriefReader(Protocol):
    def list_pod_briefs(self, namespace: str, label_selector: str) -> list[dict]:
        """[{"name", "node", "images": {컨테이너: 이미지}, "phase",
        "waiting_reason"}] -- 현재 이미지 조회(targets)와 멈춘 노드 사유 보고용."""
        ...


def image_patch_body(container: str, image: str) -> dict:
    # strategic merge patch: containers는 name을 patchMergeKey로 병합된다.
    # JSON merge patch는 배열 전체를 교체해 env/volumeMounts를 날린다(설계 §4).
    return {"spec": {"template": {"spec": {"containers": [
        {"name": container, "image": image}]}}}}


class RolloutRunner:
    def __init__(self, k8s, *, namespace):
        self._k8s = k8s
        self._ns = namespace

    def patch_image(self, *, kind, name, container, image) -> None:
        try:
            self._k8s.patch_workload(kind, name, self._ns,
                                     image_patch_body(container, image))
        except Exception as exc:
            raise ExecutionError("patch_failed", str(exc)[:200]) from exc

    def observe(self, *, kind, name):
        try:
            return self._k8s.get_workload(kind, name, self._ns)
        except Exception as exc:
            raise ExecutionError("observe_failed", str(exc)[:200]) from exc

    def pod_briefs(self, *, selector) -> list[dict]:
        # best-effort 진단 채널 -- 이것이 실패해도 롤아웃 판정을 막으면 안 된다.
        try:
            return self._k8s.list_pod_briefs(self._ns, selector)
        except Exception as exc:
            logger.warning("pod briefs failed selector=%s: %s", selector, exc)
            return []


class StubRolloutRunner:
    """클러스터가 없을 때(execution_backend != "volcano") 쓰는 결정적 페어.
    patch를 기록하고, observe는 패치된 이미지로 즉시 수렴한 정규화 dict를 준다."""
    def __init__(self):
        self.patched = []        # (kind, name, container, image)
        self._images = {}        # (kind, name) -> {container: image}

    def patch_image(self, *, kind, name, container, image) -> None:
        self.patched.append((kind, name, container, image))
        self._images.setdefault((kind, name), {})[container] = image

    def observe(self, *, kind, name):
        images = self._images.get((kind, name))
        if images is None:
            return None
        if kind == "DaemonSet":
            return {"kind": "DaemonSet", "generation": 1, "observed_generation": 1,
                    "desired_number_scheduled": 1, "updated_number_scheduled": 1,
                    "number_ready": 1, "number_unavailable": 0,
                    "number_misscheduled": 0, "images": dict(images)}
        return {"kind": "Deployment", "generation": 1, "observed_generation": 1,
                "replicas": 1, "status_replicas": 1, "updated_replicas": 1,
                "ready_replicas": 1, "conditions": [], "images": dict(images)}

    def pod_briefs(self, *, selector) -> list[dict]:
        return []
```

- [ ] **Step 4: KubernetesClient를 확장한다**

`src/dms/execution_volcano.py`의 `KubernetesClient`:

1. `__init__`에 `self._apps = None`을 더한다.
2. `_ensure`의 **기존 `if self._core is None:` 본문 안**에 `self._apps = kubernetes.client.AppsV1Api()`를 더한다. **`_apps`로 별도 가드를 만들지 않는다** — 테스트가 `_ensure`를 스텁하고 `_apps`만 주입하는 패턴이 깨진다(설계 §4).
3. 메서드 세 개를 더한다:

```python
    def patch_workload(self, kind, name, namespace, body):
        self._ensure()
        # 콘텐츠 타입을 명시한다 -- 클라이언트 내부 기본값이 JSON merge로 바뀌면
        # containers 배열이 통째로 교체돼 env/volumeMounts가 날아간다(설계 §4).
        content_type = "application/strategic-merge-patch+json"
        try:
            if kind == "Deployment":
                self._apps.patch_namespaced_deployment(
                    name, namespace, body, _content_type=content_type)
            elif kind == "DaemonSet":
                self._apps.patch_namespaced_daemon_set(
                    name, namespace, body, _content_type=content_type)
            else:
                raise ValueError(f"unsupported workload kind: {kind}")
        except Exception as exc:
            self._log_forbidden("patch", kind, name, namespace, exc)
            raise

    def get_workload(self, kind, name, namespace):
        from .rollout_status import normalize_daemonset, normalize_deployment
        self._ensure()
        try:
            if kind == "Deployment":
                obj = self._apps.read_namespaced_deployment(name, namespace)
                return normalize_deployment(obj.to_dict())
            if kind == "DaemonSet":
                obj = self._apps.read_namespaced_daemon_set(name, namespace)
                return normalize_daemonset(obj.to_dict())
            raise ValueError(f"unsupported workload kind: {kind}")
        except Exception as exc:
            # ApiException 타입 대신 status 속성으로 판별한다 -- .venv에 kubernetes가
            # 없어 테스트가 그 타입을 만들 수 없고, 클라이언트가 보는 것도 status뿐이다.
            if getattr(exc, "status", None) == 404:
                return None
            self._log_forbidden("get", kind, name, namespace, exc)
            raise

    def _log_forbidden(self, verb, kind, name, namespace, exc):
        # RBAC 거부(403)가 "객체가 없다"와 똑같이 렌더된 read_pod_log 사고의 교훈 --
        # 403은 로그에서 즉시 구분돼야 한다(설계 §4).
        if getattr(exc, "status", None) == 403:
            logger.error("workload %s forbidden(403, RBAC?) %s %s/%s",
                         verb, kind, namespace, name)

    def list_pod_briefs(self, namespace, label_selector):
        self._ensure()
        pods = self._core.list_namespaced_pod(namespace,
                                              label_selector=label_selector)
        briefs = []
        for pod in pods.items:
            reason = None
            for cs in (pod.status.container_statuses or []):
                waiting = getattr(cs.state, "waiting", None)
                if waiting is not None and waiting.reason:
                    reason = waiting.reason      # 예: ImagePullBackOff
            briefs.append({
                "name": pod.metadata.name,
                "node": pod.spec.node_name,
                "images": {c.name: c.image for c in pod.spec.containers},
                "phase": pod.status.phase or "",
                "waiting_reason": reason,
            })
        return briefs
```

`execution_volcano.py` 상단에 `logger`가 이미 있으므로 그대로 쓴다. `list_pod_briefs`는 실 클라이언트 객체 속성(`pod.metadata.name` 등)을 쓰므로 기존 클래스 주석(`pragma: no cover - 실증 대상`)과 같은 취급이다 — 이 메서드의 단위 테스트는 만들지 않는다(fake 소비자는 `RolloutRunner.pod_briefs` 테스트가 덮는다).

- [ ] **Step 5: wiring과 config에 붙인다**

`src/dms/wiring.py`에 더한다:

```python
def build_rollout_runner(settings):
    if settings.execution_backend != "volcano":
        from .rollout_runner import StubRolloutRunner
        return StubRolloutRunner()
    from .execution_volcano import KubernetesClient
    from .rollout_runner import RolloutRunner
    return RolloutRunner(KubernetesClient(settings.k8s_namespace),
                         namespace=settings.k8s_namespace)
```

`src/dms/config.py`의 `_SERVER_INT_KEYS`에 두 항목을 더한다:

```python
    # 롤아웃 루프 간격 10초 -> per-loop 리스 max(10*3, 30)=30초. 설계 §2: 리스는
    # 갱신되지 않으므로 긴 간격은 컨트롤러 자기 갱신 후 재획득을 그만큼 늦춘다.
    ("DMS_ROLLOUT_INTERVAL_SECONDS", "rollout_interval_seconds", 10),
    # DaemonSet 벽시계 타임아웃(설계 §3: conditions가 없어 이것이 유일한 실패 수단).
    # 600은 Deployment의 progressDeadlineSeconds와 같은 값 -- Deployment에는 이
    # 값의 3배를 최후 회수로만 쓴다(rollout_watcher.py 참고).
    ("DMS_ROLLOUT_TIMEOUT_SECONDS", "rollout_timeout_seconds", 600),
```

`Settings` dataclass에 `rollout_interval_seconds: int = 10`, `rollout_timeout_seconds: int = 600` 필드를 더한다 (`from_env`는 `**extra`로 이미 흘러든다).

- [ ] **Step 6: 사유 코드 목록을 갱신한다**

이 태스크의 백엔드 리터럴: `patch_failed`, `observe_failed` (ExecutionError 생성자). `frontend/src/lib/reasonCodes.json`에 두 코드를 넣고 `REASON_MESSAGES`에:

```ts
  patch_failed: "워크로드 패치에 실패했습니다 — 컨트롤러 RBAC/로그를 확인하세요",
  observe_failed: "워크로드 상태를 읽지 못했습니다",
```

- [ ] **Step 7: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_rollout_runner.py tests/test_config.py tests/test_k8s_read_pod_log.py -q`
Expected: PASS (신규 13 tests + 기존)

Run: `cd frontend && npx vitest run src/lib/reasonCodes.test.ts`
Expected: PASS

- [ ] **Step 8: 전체 스위트**

Run: `.venv/bin/python -m pytest -q` (포그라운드, timeout 400000ms)
Expected: 전부 PASS — 특히 기존 `tests/test_execution_volcano.py`(K8sClient 페어)가 그대로 통과해야 한다(Protocol을 안 건드렸다는 증거)

- [ ] **Step 9: 커밋**

```bash
git add src/dms/rollout_runner.py src/dms/execution_volcano.py src/dms/wiring.py src/dms/config.py frontend/src/lib/reasonCodes.json frontend/src/lib/api.ts tests/test_rollout_runner.py
git commit -m "feat(releases): WorkloadClient/RolloutRunner와 k8s 클라이언트 확장"
```

---
### Task 4: 롤아웃 컨트롤러 루프 (RolloutWatcher)

**Files:**
- Create: `src/dms/rollout_watcher.py`
- Modify: `src/dms/controller.py`, `src/dms/cli.py`
- Modify: `frontend/src/lib/reasonCodes.json`, `frontend/src/lib/api.ts` (`rollout_failed`, `rollout_timeout`, `rollout_aborted`, `workload_not_found`)
- Test: `tests/test_rollout_watcher.py`

**Interfaces:**
- Consumes: `ReleasesRepository`(Task 1: `active`/`mark_applying`/`finish`/`abort_pending`), `COMPONENTS` from `.repositories.releases`; `RolloutRunner`/`StubRolloutRunner`(Task 3: `patch_image`/`observe`/`pod_briefs`); `assess_deployment`/`assess_daemonset` from `.rollout_status`(Task 2); `iso_plus`/`utc_now_iso` from `.db`; `ExecutionError`.
- Produces:
  - `RolloutWatcher(repos, runner, *, timeout_seconds).run_once(*, now_iso=None) -> dict` (`{"patched": n, "finished": n}`)
  - `controller.build_loops(..., rollout_runner=None)` — `None`이면 루프를 넣지 않는다 (build_runner와 같은 하위호환)
  - `controller.run_forever(..., rollout_runner=None)`

**동작 (매 틱, 각 단계는 API 호출 1~2회로 즉시 반환 — 리스 30초를 절대 넘지 않는다):**

1. `active()`가 비면 반환 (멱등).
2. **head**(최소 seq)만 진행한다 — 순서 강제. head가 종단이 되기 전에는 뒤 행을 건드리지 않는다.
3. head가 `Pending`: `mark_applying`(커밋) → **그 다음** `patch_image` → 반환. 커밋이 먼저다 — "방금 patch를 불렀다"는 사실은 프로세스 죽음(특히 컨트롤러 자기 갱신)을 넘지 못한다(설계 §2).
4. head가 `Applying`:
   - **벽시계 회수를 observe보다 먼저** 본다(observe가 지속 실패해도 회수는 되어야 배치가 영원히 안 잠긴다). 회수 창: DaemonSet은 `timeout_seconds`, Deployment는 `timeout_seconds * 3` — Deployment의 실패 확정은 PDE(600초, 진행 시 리셋)가 하므로 벽시계는 그보다 **훨씬 긴 최후 수단**이어야 설계 §3("자체적으로 더 짧은 상한을 두지 않는다")과 충돌하지 않는다. DaemonSet 회수 시 `pod_briefs`로 멈춘 노드·사유를 모아 복합 코드 `f"rollout_timeout:{...}"`에 싣는다.
   - `observe` → `None`(404)이면 `Failed`/`workload_not_found`.
   - 관찰된 spec 이미지가 목표와 다르면 **재패치** 후 반환 — record 후 patch 전에 죽은 크래시 복구 경로. 같은 이미지 재패치는 새 ReplicaSet을 만들지 않아 멱등(설계 §2).
   - 목표 이미지면 순수 함수로 판정: `applied` → `Applied`; `failed` → `Failed`/`f"rollout_failed:{detail}"` + `abort_pending("rollout_aborted")`.
5. `ExecutionError`는 삼키고 로그만 남긴다(일시 오류는 다음 틱 재시도, build_watcher I6와 같은 관용구). 실패 확정은 판정/벽시계만 한다.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_rollout_watcher.py`:

```python
import pytest
from dms.db import Database, iso_plus, utc_now_iso
from dms.execution import ExecutionError
from dms.migrations import migrate
from dms.repositories import Repositories
from dms.rollout_watcher import RolloutWatcher


class _Runner:
    """관찰 결과를 (kind, name)별로 스크립트하는 페어."""
    def __init__(self):
        self.patched = []
        self.observations = {}     # (kind, name) -> 정규화 dict | None
        self.fail_patch = False
        self.fail_observe = False
        self.briefs = []

    def patch_image(self, *, kind, name, container, image):
        if self.fail_patch:
            raise ExecutionError("patch_failed", "boom")
        self.patched.append((kind, name, container, image))

    def observe(self, *, kind, name):
        if self.fail_observe:
            raise ExecutionError("observe_failed", "down")
        return self.observations.get((kind, name))

    def pod_briefs(self, *, selector):
        return self.briefs


def _converged_deploy(image, container="api"):
    return {"kind": "Deployment", "generation": 2, "observed_generation": 2,
            "replicas": 1, "status_replicas": 1, "updated_replicas": 1,
            "ready_replicas": 1, "conditions": [], "images": {container: image}}


def _pde_deploy(image, container="api"):
    return {"kind": "Deployment", "generation": 2, "observed_generation": 2,
            "replicas": 1, "status_replicas": 1, "updated_replicas": 0,
            "ready_replicas": 0, "images": {container: image},
            "conditions": [{"type": "Progressing", "status": "False",
                            "reason": "ProgressDeadlineExceeded", "message": "x"}]}


@pytest.fixture
def repos(tmp_path):
    db = Database.connect(f"sqlite:///{tmp_path}/t.db")
    migrate(db)
    return Repositories(db)


def _batch(repos, *components):
    items = [{"component": c,
              "image": f"pkg-01:5000/{'dms-agent' if c == 'dms-agent' else 'dms'}:new1",
              "tag": "new1"} for c in components]
    return repos.releases.create_batch(items=items, actor="ops")


def _watch(repos, runner, timeout=600):
    return RolloutWatcher(repos, runner, timeout_seconds=timeout)


def test_pending_head_is_recorded_then_patched(repos):
    rows = _batch(repos, "dms-agent", "dms-controller")
    runner = _Runner()
    out = _watch(repos, runner).run_once()
    assert out["patched"] == 1
    # 순서 강제: head(dms-agent)만 나간다 -- 컨트롤러는 아직 Pending
    assert runner.patched == [("DaemonSet", "dms-agent", "agent",
                               "pkg-01:5000/dms-agent:new1")]
    assert repos.releases.get(rows[0]["id"])["state"] == "Applying"
    assert repos.releases.get(rows[1]["id"])["state"] == "Pending"


def test_record_survives_patch_failure(repos):
    # record-then-patch: patch가 죽어도 행은 이미 Applying으로 커밋돼 있다 --
    # 다음 틱의 재패치 경로가 이어받는다(즉시 Failed를 박지 않는다)
    rows = _batch(repos, "dms-api")
    runner = _Runner()
    runner.fail_patch = True
    _watch(repos, runner).run_once()
    assert repos.releases.get(rows[0]["id"])["state"] == "Applying"


def test_applying_with_wrong_image_is_repatched(repos):
    # 크래시 복구: 기록은 있는데 패치가 안 나갔다 -- 관찰 이미지가 목표와 다르면
    # 재패치한다(같은 이미지 재패치는 멱등, 설계 §2)
    rows = _batch(repos, "dms-api")
    repos.releases.mark_applying(rows[0]["id"])
    runner = _Runner()
    runner.observations[("Deployment", "dms-api")] = _converged_deploy("pkg-01:5000/dms:old")
    out = _watch(repos, runner).run_once()
    assert out["patched"] == 1
    assert runner.patched[0][3] == "pkg-01:5000/dms:new1"
    assert repos.releases.get(rows[0]["id"])["state"] == "Applying"


def test_applying_with_target_image_converges_to_applied(repos):
    # "행은 Applying인데 클러스터가 이미 목표 이미지"는 정상 케이스다 --
    # 그것이 정확히 patch 직후 죽은 상태(컨트롤러 자기 갱신의 핵심 경로)
    rows = _batch(repos, "dms-api")
    repos.releases.mark_applying(rows[0]["id"])
    runner = _Runner()
    runner.observations[("Deployment", "dms-api")] = _converged_deploy("pkg-01:5000/dms:new1")
    out = _watch(repos, runner).run_once()
    assert out["finished"] == 1
    assert repos.releases.get(rows[0]["id"])["state"] == "Applied"
    assert runner.patched == []


def test_next_component_starts_only_after_head_is_terminal(repos):
    rows = _batch(repos, "dms-agent", "dms-api")
    repos.releases.mark_applying(rows[0]["id"])
    runner = _Runner()
    runner.observations[("DaemonSet", "dms-agent")] = {
        "kind": "DaemonSet", "generation": 1, "observed_generation": 1,
        "desired_number_scheduled": 5, "updated_number_scheduled": 5,
        "number_ready": 5, "number_unavailable": 0, "number_misscheduled": 0,
        "images": {"agent": "pkg-01:5000/dms-agent:new1"}}
    w = _watch(repos, runner)
    w.run_once()                       # agent -> Applied
    assert repos.releases.get(rows[0]["id"])["state"] == "Applied"
    w.run_once()                       # 다음 틱에야 api가 나간다
    assert ("Deployment", "dms-api", "api", "pkg-01:5000/dms:new1") in runner.patched


def test_pde_fails_release_and_aborts_the_rest(repos):
    rows = _batch(repos, "dms-api", "dms-controller")
    repos.releases.mark_applying(rows[0]["id"])
    runner = _Runner()
    runner.observations[("Deployment", "dms-api")] = _pde_deploy("pkg-01:5000/dms:new1")
    _watch(repos, runner).run_once()
    head = repos.releases.get(rows[0]["id"])
    assert head["state"] == "Failed"
    assert head["reason_code"].startswith("rollout_failed:")
    tail = repos.releases.get(rows[1]["id"])
    # 배치를 닫지 않으면 rollout_in_progress가 영원히 새 롤아웃을 막는다
    assert (tail["state"], tail["reason_code"]) == ("Failed", "rollout_aborted")


def test_missing_workload_is_failed(repos):
    rows = _batch(repos, "dms-api")
    repos.releases.mark_applying(rows[0]["id"])
    _watch(repos, _Runner()).run_once()     # observations 비어 있음 -> None
    row = repos.releases.get(rows[0]["id"])
    assert (row["state"], row["reason_code"]) == ("Failed", "workload_not_found")


def test_transient_observe_error_leaves_state_for_next_tick(repos):
    rows = _batch(repos, "dms-api")
    repos.releases.mark_applying(rows[0]["id"])
    runner = _Runner()
    runner.fail_observe = True
    _watch(repos, runner).run_once()        # 예외가 새 나가면 안 된다
    assert repos.releases.get(rows[0]["id"])["state"] == "Applying"


def test_daemonset_wallclock_reclaims_even_when_observe_fails(repos):
    # DaemonSet에는 conditions가 없어 벽시계가 유일한 실패 수단이고(설계 §3),
    # observe가 지속 실패해도 회수돼야 배치가 영원히 잠기지 않는다
    rows = _batch(repos, "dms-agent")
    repos.releases.mark_applying(rows[0]["id"])
    runner = _Runner()
    runner.fail_observe = True
    runner.briefs = [{"name": "p", "node": "dms-w3", "images": {},
                      "phase": "Pending", "waiting_reason": "ImagePullBackOff"}]
    late = iso_plus(utc_now_iso(), 601)
    _watch(repos, runner, timeout=600).run_once(now_iso=late)
    row = repos.releases.get(rows[0]["id"])
    assert row["state"] == "Failed"
    assert row["reason_code"].startswith("rollout_timeout")
    assert "ImagePullBackOff" in row["reason_code"]


def test_deployment_wallclock_is_three_times_longer(repos):
    # Deployment의 실패 확정은 PDE 몫 -- 벽시계는 3배로 물려 최후 수단으로만 쓴다
    rows = _batch(repos, "dms-api")
    repos.releases.mark_applying(rows[0]["id"])
    runner = _Runner()
    runner.fail_observe = True
    w = _watch(repos, runner, timeout=600)
    w.run_once(now_iso=iso_plus(utc_now_iso(), 601))
    assert repos.releases.get(rows[0]["id"])["state"] == "Applying"   # 아직
    w.run_once(now_iso=iso_plus(utc_now_iso(), 1801))
    assert repos.releases.get(rows[0]["id"])["state"] == "Failed"


def test_run_once_is_idempotent_when_nothing_active(repos):
    assert _watch(repos, _Runner()).run_once() == {"patched": 0, "finished": 0}
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_rollout_watcher.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'dms.rollout_watcher'`

- [ ] **Step 3: 구현한다**

`src/dms/rollout_watcher.py`:

```python
"""릴리스를 클러스터에 적용하고 수렴을 판정하는 컨트롤러 루프.

루프 안의 예외는 controller.run_all_once가 삼켜 stderr로만 내보낸다 -- 실패는
예외로 새지 않고 반드시 releases.state/reason_code로 드러나야 한다(설계 §2).
리스는 갱신되지 않으므로(간격 10초 -> 리스 30초) 모든 경로가 API 호출 한두 번으로
즉시 반환한다 -- 어디서도 기다리지 않는다."""
import logging

from .db import iso_plus, utc_now_iso
from .execution import ExecutionError
from .repositories.releases import COMPONENTS
from .rollout_status import assess_daemonset, assess_deployment

logger = logging.getLogger(__name__)

# Deployment의 실패 확정은 progressDeadlineSeconds(600, 진행 시 리셋)가 한다 --
# 벽시계를 같은 600으로 걸면 PDE보다 짧아질 수 있어 설계 §3("자체 상한을 두지
# 않는다")과 충돌한다. 3배는 상태 조회가 지속 실패할 때(RBAC 오설정 등) 배치가
# 영원히 rollout_in_progress로 잠기는 것을 푸는 최후 수단이다.
_DEPLOY_TIMEOUT_FACTOR = 3


class RolloutWatcher:
    def __init__(self, repos, runner, *, timeout_seconds):
        self._repos = repos
        self._runner = runner
        self._timeout_seconds = timeout_seconds

    def _reclaim(self, head, spec) -> bool:
        """벽시계 회수. observe보다 먼저 -- 조회가 지속 실패해도 회수는 돼야 한다."""
        factor = 1 if spec["kind"] == "DaemonSet" else _DEPLOY_TIMEOUT_FACTOR
        if head["applied_at"] < iso_plus(self._now, -self._timeout_seconds * factor):
            # 파드가 왜 멈췄는지(ImagePullBackOff 등)는 지금 모으지 않으면 영영
            # 못 본다 -- 판정 근거를 reason_code에 함께 싣는다(설계 §3).
            briefs = self._runner.pod_briefs(selector=spec["selector"])
            stuck = ",".join(sorted({f"{b.get('node')}:{b.get('waiting_reason')}"
                                     for b in briefs if b.get("waiting_reason")}))
            code = f"rollout_timeout:{stuck}"[:200] if stuck else "rollout_timeout"
            self._repos.releases.finish(head["id"], state="Failed", reason_code=code)
            self._repos.releases.abort_pending(reason_code="rollout_aborted")
            return True
        return False

    def run_once(self, *, now_iso=None) -> dict:
        patched = finished = 0
        self._now = now_iso or utc_now_iso()
        active = self._repos.releases.active()
        if not active:
            return {"patched": 0, "finished": 0}
        # 순서 강제: head(최소 seq)만 진행한다. dms-agent -> dms-api ->
        # dms-controller 순서가 seq로 지속돼 있어(Task 1), 자기 갱신으로 죽은
        # 컨트롤러의 후임도 이미 끝낸 패치를 다시 하지 않는다(설계 §2).
        head = active[0]
        spec = COMPONENTS[head["component"]]
        try:
            if head["state"] == "Pending":
                # 1단계: 기록을 먼저 커밋한다. "방금 patch를 불렀다"는 사실은
                # 프로세스 죽음(특히 컨트롤러 자기 갱신)을 넘지 못한다(설계 §2).
                self._repos.releases.mark_applying(head["id"])
                self._runner.patch_image(kind=spec["kind"], name=spec["workload"],
                                         container=spec["container"],
                                         image=head["image"])
                patched += 1
                return {"patched": patched, "finished": finished}

            # state == Applying
            if self._reclaim(head, spec):
                return {"patched": patched, "finished": finished + 1}
            obs = self._runner.observe(kind=spec["kind"], name=spec["workload"])
            if obs is None:
                self._repos.releases.finish(head["id"], state="Failed",
                                            reason_code="workload_not_found")
                self._repos.releases.abort_pending(reason_code="rollout_aborted")
                return {"patched": patched, "finished": finished + 1}
            if obs["images"].get(spec["container"]) != head["image"]:
                # 크래시 복구: 행은 Applying인데 spec 이미지가 목표가 아니다 --
                # record 후 patch 전에 죽었다. 같은 이미지 재패치는 새 ReplicaSet을
                # 만들지 않으므로 그냥 다시 패치한다(설계 §2 멱등성 요구).
                self._runner.patch_image(kind=spec["kind"], name=spec["workload"],
                                         container=spec["container"],
                                         image=head["image"])
                return {"patched": patched + 1, "finished": finished}
            verdict, detail = (assess_deployment(obs)
                               if spec["kind"] == "Deployment"
                               else assess_daemonset(obs))
            if verdict == "applied":
                self._repos.releases.finish(head["id"], state="Applied")
                finished += 1
            elif verdict == "failed":
                code = f"rollout_failed:{detail}"[:200] if detail else "rollout_failed"
                self._repos.releases.finish(head["id"], state="Failed",
                                            reason_code=code)
                self._repos.releases.abort_pending(reason_code="rollout_aborted")
                finished += 1
            # progressing이면 아무것도 안 한다 -- 다음 틱이 다시 본다
        except ExecutionError as exc:
            # 일시 오류(apiserver 재시작 등)로 즉시 Failed를 박지 않는다 --
            # build_watcher I6와 같은 관용구. 영구 오류는 위 벽시계가 회수한다.
            logger.warning("rollout watcher error release=%s: %s",
                           head["id"], exc)
        return {"patched": patched, "finished": finished}
```

- [ ] **Step 4: 컨트롤러와 cli에 배선한다**

`src/dms/controller.py`:
- `from .rollout_watcher import RolloutWatcher` import를 더한다.
- `build_loops` 시그니처를 `def build_loops(settings, repos, *, identity_resolver=None, execution_adapter=None, build_runner=None, rollout_runner=None)`로 바꾼다.
- `build_runner` 블록 아래에 같은 패턴으로 더한다:

```python
    # rollout_runner가 없으면(기존 호출자) 루프를 아예 넣지 않는다 --
    # build-watcher와 같은 하위호환 규칙이다.
    if rollout_runner is not None:
        loops.append(Loop("rollout-watcher", settings.rollout_interval_seconds,
                          lambda: RolloutWatcher(
                              repos, rollout_runner,
                              timeout_seconds=settings.rollout_timeout_seconds
                          ).run_once()))
```

- `run_forever` 시그니처에도 `rollout_runner=None`을 더해 `build_loops`로 넘긴다.

`src/dms/cli.py`의 controller 분기:
- import에 `build_rollout_runner`를 더하고, `rollout_runner = build_rollout_runner(settings)`를 만든 뒤 `build_loops(...)`/`run_forever(...)` 호출에 `rollout_runner=rollout_runner`를 넘긴다.

- [ ] **Step 5: 사유 코드 목록을 갱신한다**

이 태스크의 백엔드 리터럴: `workload_not_found`, `rollout_aborted`는 `reason_code="..."` 직접 리터럴이라 AST 추출에 잡힌다. `rollout_failed`/`rollout_timeout`은 `code` 변수 경유(`reason_code=code`)라 추출에는 안 잡히지만, 화면이 `reasonText()`로 번역하려면 매핑이 필요하고 커버리지 테스트(reasonCodes.test.ts)는 json↔REASON_MESSAGES 짝만 검사하므로(json이 추출 결과의 상위집합이어도 `test_reason_codes_coverage.py`는 추출⊆json 방향만 본다) 네 코드 전부 넣는다. `reasonCodes.json`에 `"rollout_failed", "rollout_timeout", "rollout_aborted", "workload_not_found"`를, `REASON_MESSAGES`에:

```ts
  rollout_failed: "롤아웃이 실패했습니다",
  rollout_timeout: "롤아웃이 제한 시간을 넘겨 실패했습니다",
  rollout_aborted: "앞 컴포넌트 실패로 롤아웃이 중단되었습니다",
  workload_not_found: "대상 워크로드를 찾을 수 없습니다",
```

(복합 코드 `rollout_failed:ProgressDeadlineExceeded: ...`는 슬라이스 12의 `reasonText()` 접두 규칙이 "롤아웃이 실패했습니다 (ProgressDeadlineExceeded: ...)"로 렌더한다 — 프론트 추가 작업 불필요.)

- [ ] **Step 6: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_rollout_watcher.py tests/test_controller.py tests/test_cli.py -q`
Expected: PASS (신규 11 tests + 기존 컨트롤러/CLI 테스트)

Run: `cd frontend && npx vitest run src/lib/reasonCodes.test.ts`
Expected: PASS

- [ ] **Step 7: 전체 스위트**

Run: `.venv/bin/python -m pytest -q` (포그라운드, timeout 400000ms)
Expected: 전부 PASS

- [ ] **Step 8: 커밋**

```bash
git add src/dms/rollout_watcher.py src/dms/controller.py src/dms/cli.py frontend/src/lib/reasonCodes.json frontend/src/lib/api.ts tests/test_rollout_watcher.py
git commit -m "feat(releases): 롤아웃 컨트롤러 루프 -- record-then-patch와 순서 강제"
```

---
### Task 5: admin 릴리스 API 3종 + 레지스트리 태그 조회

**Files:**
- Create: `src/dms/registry.py`
- Create: `src/dms/api/routes_releases.py`
- Modify: `src/dms/api/app.py`
- Modify: `frontend/src/lib/reasonCodes.json`, `frontend/src/lib/api.ts` (`unknown_component`, `unknown_tag`, `same_tag`)
- Test: `tests/test_registry.py`, `tests/test_api_releases.py`

**Interfaces:**
- Consumes: `require_admin`/`Identity`/`audit_actor` from `.auth`; `reject_when_maintenance` from `.routes_requests`; `ReleasesRepository`(Task 1), `COMPONENTS`/`ROLLOUT_ORDER`; `app.state.rollout_runner`(`pod_briefs`만 사용 — api Role에는 apps RBAC이 없으므로 이 라우터는 `patch_image`/`observe`를 **절대 부르지 않는다**); `DomainValidationError`.
- Produces:
  - `fetch_repo_tags(registry: str, repository: str) -> list[str] | None` in `src/dms/registry.py` — 성공 시 **정렬된** 태그 목록, 실패 시 `None`(예외를 올리지 않는다)
  - `app.state.rollout_runner = build_rollout_runner(settings)` (stub 백엔드에서는 `StubRolloutRunner` — `pod_briefs`가 `[]`를 줘 현재 이미지는 `null`)

| 메서드 | 경로 | 응답 |
|---|---|---|
| GET | `/api/admin/releases` | `{"current": {component: row}, "history": [row, ...]}` (최신순) |
| GET | `/api/admin/releases/targets` | `{"targets": [{component, kind, workload, container, repository, current_image, current_images, tags}], "registry_ok": bool}` |
| POST | `/api/admin/releases` | `{"items": [row, ...]}` `202` — 순서는 서버(`create_batch`)가 강제 |

거부: 유지보수 → `503 maintenance_mode`; 빈 items/모르는 component/중복 component → `422 unknown_component`; 태그 형식 불량 또는 레지스트리에 없음(**레지스트리가 응답할 때만**) → `422 unknown_tag`; 현재 클러스터 이미지와 동일(**현재 이미지를 읽을 수 있을 때만** — `IfNotPresent`라 재적용은 no-op) → `422 same_tag`; 활성 롤아웃 존재 → `409 rollout_in_progress`.

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`tests/test_registry.py`:

```python
from dms.registry import fetch_repo_tags


def test_tags_are_sorted_and_deterministic(monkeypatch):
    def fake_get_json(url, timeout):
        assert url == "http://pkg-01:5000/v2/dms/tags/list"
        return {"name": "dms", "tags": ["d3", "d1", "d2"]}
    monkeypatch.setattr("dms.registry._get_json", fake_get_json)
    assert fetch_repo_tags("pkg-01:5000", "dms") == ["d1", "d2", "d3"]


def test_failure_returns_none_not_raises(monkeypatch):
    def boom(url, timeout):
        raise OSError("connection refused")
    monkeypatch.setattr("dms.registry._get_json", boom)
    assert fetch_repo_tags("pkg-01:5000", "dms") is None


def test_malformed_body_returns_none(monkeypatch):
    monkeypatch.setattr("dms.registry._get_json", lambda url, timeout: {"tags": None})
    assert fetch_repo_tags("pkg-01:5000", "dms") is None
```

`tests/test_api_releases.py` — `tests/conftest.py`의 `client` 픽스처와 `tests/test_api_builds.py`의 ADMIN 헤더 패턴을 그대로 쓴다:

```python
import pytest

ADMIN = {"Authorization": "Bearer tok-shared", "x-dms-actor": "ops"}


class _FakeRunner:
    """pod_briefs만 쓰인다 -- api는 patch/observe를 부르면 안 된다(RBAC 없음)."""
    def __init__(self, images=None):
        self._images = images or {}   # selector -> {container: image}

    def pod_briefs(self, *, selector):
        images = self._images.get(selector)
        if images is None:
            return []
        return [{"name": "p", "node": "w1", "images": images,
                 "phase": "Running", "waiting_reason": None}]

    def patch_image(self, **kw):
        raise AssertionError("api must never patch")

    def observe(self, **kw):
        raise AssertionError("api must never observe via apps")


@pytest.fixture
def rollout_client(client, monkeypatch):
    # 레지스트리: dms/dms-agent 둘 다 응답. 개별 테스트가 monkeypatch로 덮는다.
    monkeypatch.setattr(
        "dms.api.routes_releases.fetch_repo_tags",
        lambda registry, repo: {"dms": ["d22", "d23"],
                                "dms-agent": ["dev5", "dev6"]}.get(repo))
    client.app.state.rollout_runner = _FakeRunner({
        "app.kubernetes.io/name=dms-api": {"api": "pkg-01:5000/dms:d22"},
        "app.kubernetes.io/name=dms-controller": {"controller": "pkg-01:5000/dms:d22"},
        "app.kubernetes.io/name=dms-agent": {"agent": "pkg-01:5000/dms-agent:dev5"},
    })
    return client


def test_targets_expose_current_images_and_tags(rollout_client):
    r = rollout_client.get("/api/admin/releases/targets", headers=ADMIN)
    assert r.status_code == 200
    body = r.json()
    assert body["registry_ok"] is True
    by_comp = {t["component"]: t for t in body["targets"]}
    assert list(by_comp) == ["dms-agent", "dms-api", "dms-controller"]
    assert by_comp["dms-agent"]["current_image"] == "pkg-01:5000/dms-agent:dev5"
    assert by_comp["dms-agent"]["tags"] == ["dev5", "dev6"]
    assert by_comp["dms-controller"]["container"] == "controller"


def test_targets_survive_registry_outage(rollout_client, monkeypatch):
    # 레지스트리가 죽어도 화면 전체가 죽으면 안 된다(설계 §7) -- 빈 목록 + 경고
    monkeypatch.setattr("dms.api.routes_releases.fetch_repo_tags",
                        lambda registry, repo: None)
    r = rollout_client.get("/api/admin/releases/targets", headers=ADMIN)
    assert r.status_code == 200
    assert r.json()["registry_ok"] is False
    assert all(t["tags"] == [] for t in r.json()["targets"])


def test_submit_orders_components_server_side(rollout_client):
    r = rollout_client.post(
        "/api/admin/releases",
        json={"items": [{"component": "dms-controller", "tag": "d23"},
                        {"component": "dms-agent", "tag": "dev6"}]},
        headers=ADMIN)
    assert r.status_code == 202
    items = r.json()["items"]
    assert [i["component"] for i in items] == ["dms-agent", "dms-controller"]
    assert items[0]["image"] == "pkg-01:5000/dms-agent:dev6"
    assert all(i["state"] == "Pending" for i in items)


def test_unknown_component_and_duplicates_rejected(rollout_client):
    r = rollout_client.post("/api/admin/releases",
                            json={"items": [{"component": "nope", "tag": "t"}]},
                            headers=ADMIN)
    assert r.status_code == 422 and r.json()["detail"] == "unknown_component"
    r = rollout_client.post(
        "/api/admin/releases",
        json={"items": [{"component": "dms-api", "tag": "d23"},
                        {"component": "dms-api", "tag": "d23"}]},
        headers=ADMIN)
    assert r.status_code == 422 and r.json()["detail"] == "unknown_component"
    r = rollout_client.post("/api/admin/releases", json={"items": []}, headers=ADMIN)
    assert r.status_code == 422 and r.json()["detail"] == "unknown_component"


def test_unknown_tag_enforced_only_when_registry_answers(rollout_client, monkeypatch):
    r = rollout_client.post("/api/admin/releases",
                            json={"items": [{"component": "dms-api", "tag": "ghost"}]},
                            headers=ADMIN)
    assert r.status_code == 422 and r.json()["detail"] == "unknown_tag"
    # 레지스트리 침묵 -> 통과시키고 ImagePullBackOff로 드러나게 한다(잘못된 차단보다 낫다)
    monkeypatch.setattr("dms.api.routes_releases.fetch_repo_tags",
                        lambda registry, repo: None)
    r = rollout_client.post("/api/admin/releases",
                            json={"items": [{"component": "dms-api", "tag": "ghost"}]},
                            headers=ADMIN)
    assert r.status_code == 202


def test_same_tag_is_rejected(rollout_client):
    # IfNotPresent 함정: 같은 태그 재적용은 아무 일도 안 일어난다
    r = rollout_client.post("/api/admin/releases",
                            json={"items": [{"component": "dms-api", "tag": "d22"}]},
                            headers=ADMIN)
    assert r.status_code == 422 and r.json()["detail"] == "same_tag"


def test_same_tag_skipped_when_current_unreadable(rollout_client):
    # 파드가 안 보이면(briefs 비어 있음) fail-open -- 레지스트리와 같은 원칙
    rollout_client.app.state.rollout_runner = _FakeRunner()
    r = rollout_client.post("/api/admin/releases",
                            json={"items": [{"component": "dms-api", "tag": "d22"}]},
                            headers=ADMIN)
    assert r.status_code == 202


def test_concurrent_rollout_is_409(rollout_client):
    rollout_client.post("/api/admin/releases",
                        json={"items": [{"component": "dms-api", "tag": "d23"}]},
                        headers=ADMIN)
    r = rollout_client.post("/api/admin/releases",
                            json={"items": [{"component": "dms-agent", "tag": "dev6"}]},
                            headers=ADMIN)
    assert r.status_code == 409 and r.json()["detail"] == "rollout_in_progress"


def test_submit_rejected_during_maintenance(rollout_client):
    rollout_client.put("/api/admin/control-state",
                       json={"maintenance": True, "drain": False, "reason": "정비"},
                       headers=ADMIN)
    r = rollout_client.post("/api/admin/releases",
                            json={"items": [{"component": "dms-api", "tag": "d23"}]},
                            headers=ADMIN)
    assert r.status_code == 503 and r.json()["detail"] == "maintenance_mode"


def test_list_carries_current_and_history(rollout_client):
    rollout_client.post("/api/admin/releases",
                        json={"items": [{"component": "dms-api", "tag": "d23"}]},
                        headers=ADMIN)
    r = rollout_client.get("/api/admin/releases", headers=ADMIN)
    assert r.status_code == 200
    body = r.json()
    assert body["current"]["dms-api"]["tag"] == "d23"
    assert body["history"][0]["component"] == "dms-api"


def test_submit_writes_release_audit_with_actor(rollout_client):
    rollout_client.post("/api/admin/releases",
                        json={"items": [{"component": "dms-api", "tag": "d23"}]},
                        headers=ADMIN)
    entries = rollout_client.app.state.repos.control.audit_entries(limit=3)
    entry = next(e for e in entries if e["mutation_class"] == "release")
    # 공유 토큰 인증은 token: 접두(슬라이스 12 audit_actor)
    assert entry["actor"] == "token:ops"


def test_releases_are_admin_only(client):
    client.post("/api/auth/signup", json={"username": "u1", "password": "p"})
    client.post("/api/auth/login", json={"username": "u1", "password": "p"})
    assert client.get("/api/admin/releases").status_code in (401, 403)
```

- [ ] **Step 2: 실패를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_registry.py tests/test_api_releases.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'dms.registry'` / 404 (라우트 없음)

- [ ] **Step 3: registry 모듈을 만든다**

`src/dms/registry.py`:

```python
"""컨테이너 레지스트리 v2 태그 조회. 실패 내성이 계약이다 -- 레지스트리가 죽었다고
롤아웃 화면 전체가 죽으면 안 된다(설계 §7). 실패는 None으로 알리고, 호출자가
빈 목록+경고로 강등하거나(targets) 검증을 건너뛴다(unknown_tag)."""
import json
import logging
import urllib.request

logger = logging.getLogger(__name__)

# 폴링 화면에서 불리므로 짧게 -- 레지스트리 행업이 api 워커를 물고 있으면 안 된다.
_TIMEOUT_SECONDS = 3.0


def _get_json(url: str, timeout: float):
    # 테스트 심(seam) -- monkeypatch 지점을 한 곳으로 모은다.
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_repo_tags(registry: str, repository: str) -> "list[str] | None":
    # 레지스트리는 평문 HTTP다(빌드 스크립트가 --tls-verify=false를 쓰는 그 레지스트리).
    url = f"http://{registry}/v2/{repository}/tags/list"
    try:
        data = _get_json(url, _TIMEOUT_SECONDS)
    except Exception as exc:
        logger.warning("registry tags fetch failed repo=%s: %s", repository, exc)
        return None
    tags = data.get("tags") if isinstance(data, dict) else None
    if not isinstance(tags, list):
        return None
    # 정렬해 결정적으로 만든다 -- 레지스트리 응답 순서는 보장이 없다.
    return sorted(str(t) for t in tags)
```

- [ ] **Step 4: 라우터를 만든다**

`src/dms/api/routes_releases.py`:

```python
import re

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from ..domain import DomainValidationError
from ..registry import fetch_repo_tags
from ..repositories.releases import COMPONENTS, ROLLOUT_ORDER
from .auth import Identity, audit_actor, require_admin
from .routes_requests import reject_when_maintenance

router = APIRouter(dependencies=[Depends(require_admin)])

# docker 태그 문자셋. 이 값은 이미지 참조 문자열에 그대로 들어간다.
_TAG_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]{0,127}")


class ReleaseItem(BaseModel):
    component: str
    tag: str


class ReleaseBody(BaseModel):
    items: list[ReleaseItem]


def _current_images(runner, spec) -> list[str]:
    # 현재 이미지는 apps API가 아니라 파드 목록에서 읽는다 -- api Role에는 apps
    # get이 없다(설계 §5는 apps RBAC을 컨트롤러에만 부여). pods list는 이미 있다.
    briefs = runner.pod_briefs(selector=spec["selector"])
    images = {b["images"].get(spec["container"]) for b in briefs}
    return sorted(i for i in images if i)


@router.get("/api/admin/releases")
def list_releases(request: Request,
                  limit: int = Query(default=50, ge=1, le=200)):
    repos = request.app.state.repos
    return {"current": repos.releases.current(),
            "history": repos.releases.list(limit=limit)}


@router.get("/api/admin/releases/targets")
def release_targets(request: Request):
    settings = request.app.state.settings
    runner = request.app.state.rollout_runner
    registry_ok = True
    tags_cache: dict = {}
    targets = []
    for component in ROLLOUT_ORDER:
        spec = COMPONENTS[component]
        repo_name = spec["repository"]
        if repo_name not in tags_cache:     # api/controller가 같은 dms 리포를 쓴다
            tags_cache[repo_name] = fetch_repo_tags(settings.build_registry, repo_name)
        tags = tags_cache[repo_name]
        if tags is None:
            registry_ok = False
        images = _current_images(runner, spec)
        targets.append({
            "component": component, "kind": spec["kind"],
            "workload": spec["workload"], "container": spec["container"],
            "repository": repo_name,
            # 롤링 중이면 이미지가 2종일 수 있다 -- 단일일 때만 current_image를 준다
            "current_image": images[0] if len(images) == 1 else None,
            "current_images": images,
            "tags": tags or [],
        })
    return {"targets": targets, "registry_ok": registry_ok}


@router.post("/api/admin/releases", status_code=202)
def submit_releases(body: ReleaseBody, request: Request,
                    identity: Identity = Depends(require_admin)):
    reject_when_maintenance(request)
    repos = request.app.state.repos
    settings = request.app.state.settings
    runner = request.app.state.rollout_runner
    if not body.items:
        raise HTTPException(status_code=422, detail="unknown_component")
    seen = set()
    for item in body.items:
        # 중복 component도 여기로 -- 한 배치에 같은 워크로드 패치 2건은 뒤가 앞을
        # 조용히 덮어 순서 의미가 깨진다.
        if item.component not in COMPONENTS or item.component in seen:
            raise HTTPException(status_code=422, detail="unknown_component")
        seen.add(item.component)
    # 빠른 거절(fail-fast) -- 진짜 가드는 create_batch의 트랜잭션 안에 있다.
    if repos.releases.active():
        raise HTTPException(status_code=409, detail="rollout_in_progress")
    tags_cache: dict = {}
    records = []
    for item in body.items:
        spec = COMPONENTS[item.component]
        tag = (item.tag or "").strip()
        if not _TAG_RE.fullmatch(tag):
            raise HTTPException(status_code=422, detail="unknown_tag")
        repo_name = spec["repository"]
        if repo_name not in tags_cache:
            tags_cache[repo_name] = fetch_repo_tags(settings.build_registry, repo_name)
        tags = tags_cache[repo_name]
        # 레지스트리가 응답할 때만 강제한다 -- 응답 불가면 통과시키고 잘못된 태그는
        # patch 후 ImagePullBackOff로 드러나게 한다(잘못된 차단보다 낫다, 설계 §7).
        if tags is not None and tag not in tags:
            raise HTTPException(status_code=422, detail="unknown_tag")
        image = f"{settings.build_registry}/{repo_name}:{tag}"
        current = _current_images(runner, spec)
        # IfNotPresent 함정: 같은 태그 재적용은 아무 일도 안 일어난다(설계 §7).
        # 현재 이미지를 못 읽으면(파드 0개 등) 검사를 건너뛴다 -- fail-open.
        if current == [image]:
            raise HTTPException(status_code=422, detail="same_tag")
        records.append({"component": item.component, "image": image, "tag": tag})
    try:
        rows = repos.releases.create_batch(items=records,
                                           actor=audit_actor(identity))
    except DomainValidationError as e:
        # 사전 체크와 이 사이의 경합 창 -- 트랜잭션 안 가드가 잡는다.
        raise HTTPException(status_code=409, detail=e.reason_code)
    return {"items": rows}
```

- [ ] **Step 5: 앱에 등록한다**

`src/dms/api/app.py`:
- `from .routes_releases import router as releases_router` import를 더하고 `app.include_router(releases_router)` (builds_router 다음).
- `app.state.build_runner` 아래에 `app.state.rollout_runner = build_rollout_runner(settings)`를 더한다 (`from ..wiring import build_rollout_runner` — 기존 wiring import 줄에 합친다).

- [ ] **Step 6: 사유 코드 목록을 갱신한다**

이 태스크의 백엔드 리터럴: `unknown_component`, `unknown_tag`, `same_tag` (HTTPException detail=). `reasonCodes.json`에 세 코드를 넣고 `REASON_MESSAGES`에:

```ts
  unknown_component: "알 수 없는 컴포넌트이거나 중복 선택입니다",
  unknown_tag: "레지스트리에 없는 태그입니다",
  same_tag: "현재 태그와 같습니다 — IfNotPresent라 재적용해도 아무 일도 일어나지 않습니다",
```

- [ ] **Step 7: 통과를 확인한다**

Run: `.venv/bin/python -m pytest tests/test_registry.py tests/test_api_releases.py -q`
Expected: PASS (3 + 12 tests)

Run: `cd frontend && npx vitest run src/lib/reasonCodes.test.ts`
Expected: PASS

- [ ] **Step 8: 전체 스위트**

Run: `.venv/bin/python -m pytest -q` (포그라운드, timeout 400000ms)
Expected: 전부 PASS

- [ ] **Step 9: 커밋**

```bash
git add src/dms/registry.py src/dms/api/routes_releases.py src/dms/api/app.py frontend/src/lib/reasonCodes.json frontend/src/lib/api.ts tests/test_registry.py tests/test_api_releases.py
git commit -m "feat(api): admin 릴리스 엔드포인트와 레지스트리 태그 조회"
```

---
### Task 6: 포탈 「릴리스」 화면

**Files:**
- Create: `frontend/src/features/releases/useReleases.ts`
- Create: `frontend/src/features/releases/ReleasesPage.tsx`
- Create: `frontend/src/features/releases/ReleasesPage.test.tsx`
- Modify: `frontend/src/lib/types.ts`, `frontend/src/lib/api.ts`, `frontend/src/lib/reasonCodes.json` (`registry_unreachable` — 프론트 전용 경고 코드)
- Modify: `frontend/src/app/router.tsx`, `frontend/src/app/AppShell.tsx`

**Interfaces:**
- Consumes: `apiGet`/`apiSend`/`ApiError`/`reasonText` from `../../lib/api`; `Card`/`Button`/`Table` from `../../components/ui/*`; Task 5의 응답 형태.
- Produces: `useReleaseTargets()`, `useReleases()`, `useSubmitReleases()`, `ReleasesPage`, 라우트 `/admin/releases`, 내비 링크 **릴리스**.

**화면 계약 (테스트가 고정한다):**
- h1은 정확히 `릴리스`.
- 컴포넌트 3행: 이름 / 현재 이미지(단일이 아니면 `current_images`를 `, `로 병기, 없으면 `—`) / 새 태그 select(`aria-label`은 컴포넌트 이름 그대로, 첫 옵션은 `변경 없음`(값 `""`)) / 상태(해당 컴포넌트의 현재 릴리스 상태 + 사유 `reasonText`).
- 제출 버튼 이름은 `롤아웃 시작` — 선택이 하나도 없으면 비활성. 선택된 것만 `{items: [{component, tag}]}` **한 배치**로 POST(순서는 서버가 강제).
- **경고 문구는 항상 보인다**: `컨트롤러를 갱신하면 컨트롤러가 재시작되어 롤아웃 추적이 잠시 끊깁니다 — 화면이 멈춘 것은 장애가 아닙니다.` (설계 §8: 운영자가 자기유발 정지를 장애로 오해하면 안 된다.)
- `registry_ok === false`면 `reasonText("registry_unreachable")` 경고를 보여주되 화면은 산다.
- 이력 표 컬럼: 시각 / 컴포넌트 / 태그 / 상태 / 사유(`reasonText`) / actor. null은 `—`.
- 진행 중(`Pending`/`Applying`) 행이 있으면 `진행 중` 배지를 보여주고 5초 폴링, 전부 종단이면 폴링 정지(useBuilds의 조건부 `refetchInterval` 관용구).
- 모든 응답은 렌더 전에 방어적으로 정규화: `Array.isArray(data?.targets)`, `Array.isArray(data?.history)`, `current ?? {}`.

- [ ] **Step 1: 타입과 사유 코드를 더한다**

`frontend/src/lib/types.ts`에:

```ts
export interface Release {
  id: number; component: string; image: string; tag: string;
  digest: string | null; state: string; reason_code: string | null;
  seq?: number; actor: string; applied_at: string;
}
export interface ReleaseTarget {
  component: string; kind: string; workload: string; container: string;
  repository: string; current_image: string | null; current_images: string[];
  tags: string[];
}
export interface ReleaseTargets { targets: ReleaseTarget[]; registry_ok: boolean }
export interface Releases { current: Record<string, Release>; history: Release[] }
```

`frontend/src/lib/reasonCodes.json`에 `"registry_unreachable"`(백엔드 리터럴은 아니지만 커버리지 테스트는 json↔매핑 짝만 요구한다), `REASON_MESSAGES`에:

```ts
  registry_unreachable: "레지스트리에 연결할 수 없어 태그 목록이 비어 있습니다",
```

- [ ] **Step 2: 실패하는 테스트를 쓴다**

`frontend/src/features/releases/ReleasesPage.test.tsx` — `BuildsPage.test.tsx`의 MSW/QueryClient/MemoryRouter 래퍼 패턴 그대로:

```tsx
import { describe, it, expect, beforeAll, afterEach, afterAll } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { ReleasesPage } from "./ReleasesPage";

const TARGETS = {
  registry_ok: true,
  targets: [
    { component: "dms-agent", kind: "DaemonSet", workload: "dms-agent",
      container: "agent", repository: "dms-agent",
      current_image: "pkg-01:5000/dms-agent:dev5",
      current_images: ["pkg-01:5000/dms-agent:dev5"], tags: ["dev5", "dev6"] },
    { component: "dms-api", kind: "Deployment", workload: "dms-api",
      container: "api", repository: "dms",
      current_image: "pkg-01:5000/dms:d22",
      current_images: ["pkg-01:5000/dms:d22"], tags: ["d22", "d23"] },
    { component: "dms-controller", kind: "Deployment", workload: "dms-controller",
      container: "controller", repository: "dms",
      current_image: "pkg-01:5000/dms:d22",
      current_images: ["pkg-01:5000/dms:d22"], tags: ["d22", "d23"] },
  ],
};
const HISTORY = {
  current: {
    "dms-api": { id: 1, component: "dms-api", image: "pkg-01:5000/dms:d22",
                 tag: "d22", digest: null, state: "Applied", reason_code: null,
                 actor: "ops", applied_at: "2026-08-06T00:00:00Z" },
  },
  history: [
    { id: 1, component: "dms-api", image: "pkg-01:5000/dms:d22", tag: "d22",
      digest: null, state: "Applied", reason_code: null, actor: "ops",
      applied_at: "2026-08-06T00:00:00Z" },
    { id: 2, component: "dms-agent", image: "pkg-01:5000/dms-agent:dev4",
      tag: "dev4", digest: null, state: "Failed",
      reason_code: "rollout_timeout", actor: "ops",
      applied_at: "2026-08-05T00:00:00Z" },
  ],
};

const server = setupServer(
  http.get("/api/admin/releases/targets", () => HttpResponse.json(TARGETS)),
  http.get("/api/admin/releases", () => HttpResponse.json(HISTORY)),
);
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const wrap = (ui: React.ReactNode) => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><MemoryRouter>{ui}</MemoryRouter></QueryClientProvider>);
};

describe("ReleasesPage", () => {
  it("세 컴포넌트와 현재 이미지를 렌더한다", async () => {
    wrap(<ReleasesPage />);
    expect(await screen.findByRole("heading", { name: "릴리스" })).toBeInTheDocument();
    expect(screen.getByText("pkg-01:5000/dms-agent:dev5")).toBeInTheDocument();
    expect(screen.getByLabelText("dms-controller")).toBeInTheDocument();
  });

  it("컨트롤러 재시작 경고를 항상 보여준다", async () => {
    wrap(<ReleasesPage />);
    expect(await screen.findByText(/컨트롤러가 재시작되어 롤아웃 추적이 잠시 끊깁니다/))
      .toBeInTheDocument();
  });

  it("선택이 없으면 제출이 비활성이고, 선택한 것만 한 배치로 보낸다", async () => {
    let posted: unknown = null;
    server.use(http.post("/api/admin/releases", async ({ request }) => {
      posted = await request.json();
      return HttpResponse.json({ items: [] }, { status: 202 });
    }));
    wrap(<ReleasesPage />);
    await screen.findByRole("heading", { name: "릴리스" });
    expect(screen.getByRole("button", { name: "롤아웃 시작" })).toBeDisabled();
    await userEvent.selectOptions(screen.getByLabelText("dms-agent"), "dev6");
    await userEvent.click(screen.getByRole("button", { name: "롤아웃 시작" }));
    await waitFor(() => expect(posted).toEqual({
      items: [{ component: "dms-agent", tag: "dev6" }] }));
  });

  it("서버 거절을 한국어로 보여준다", async () => {
    server.use(http.post("/api/admin/releases", () =>
      HttpResponse.json({ detail: "same_tag" }, { status: 422 })));
    wrap(<ReleasesPage />);
    await screen.findByRole("heading", { name: "릴리스" });
    await userEvent.selectOptions(screen.getByLabelText("dms-agent"), "dev5");
    await userEvent.click(screen.getByRole("button", { name: "롤아웃 시작" }));
    expect(await screen.findByText(/현재 태그와 같습니다/)).toBeInTheDocument();
  });

  it("이력의 사유 코드를 번역한다", async () => {
    wrap(<ReleasesPage />);
    expect(await screen.findByText("롤아웃이 제한 시간을 넘겨 실패했습니다"))
      .toBeInTheDocument();
  });

  it("진행 중이면 배지를 보여준다", async () => {
    server.use(http.get("/api/admin/releases", () => HttpResponse.json({
      current: {},
      history: [{ id: 3, component: "dms-agent", image: "i", tag: "t",
                  digest: null, state: "Applying", reason_code: null,
                  actor: "ops", applied_at: "2026-08-06T01:00:00Z" }] })));
    wrap(<ReleasesPage />);
    expect(await screen.findByText("진행 중")).toBeInTheDocument();
  });

  it("레지스트리가 죽어도 화면이 산다", async () => {
    server.use(http.get("/api/admin/releases/targets", () =>
      HttpResponse.json({ registry_ok: false,
                          targets: TARGETS.targets.map(t => ({ ...t, tags: [] })) })));
    wrap(<ReleasesPage />);
    expect(await screen.findByText(/레지스트리에 연결할 수 없어/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "릴리스" })).toBeInTheDocument();
  });

  it("응답이 배열이 아니어도 흰 화면이 되지 않는다", async () => {
    server.use(
      http.get("/api/admin/releases/targets", () => HttpResponse.json({ oops: 1 })),
      http.get("/api/admin/releases", () => HttpResponse.json(null)),
    );
    wrap(<ReleasesPage />);
    expect(await screen.findByRole("heading", { name: "릴리스" })).toBeInTheDocument();
  });
});
```

- [ ] **Step 3: 실패를 확인한다**

Run: `cd frontend && npx vitest run src/features/releases/ReleasesPage.test.tsx`
Expected: FAIL — `Failed to resolve import "./ReleasesPage"`

- [ ] **Step 4: 훅을 만든다**

`frontend/src/features/releases/useReleases.ts`:

```ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "../../lib/api";
import type { Releases, ReleaseTargets } from "../../lib/types";

// Pending/Applying이 릴리스의 비종단 상태다 -- jobState.ts의 isTerminal은
// Applied를 몰라서 쓸 수 없다(Applied가 비종단으로 읽혀 폴링이 안 멈춘다).
export const RELEASE_ACTIVE_STATES = new Set(["Pending", "Applying"]);

export const useReleaseTargets = () =>
  useQuery({
    queryKey: ["release-targets"],
    queryFn: () => apiGet<ReleaseTargets>("/api/admin/releases/targets"),
  });

export const useReleases = () =>
  useQuery({
    queryKey: ["releases"],
    queryFn: () => apiGet<Releases>("/api/admin/releases"),
    // 진행 중일 때만 폴링 -- useBuilds와 같은 관용구. 전부 종단이면 상태가 더
    // 바뀔 일이 없고, 제출이 쿼리를 무효화하면 폴링이 자동 재개된다.
    refetchInterval: (q) => {
      const history = (q.state.data as Releases | undefined)?.history;
      return Array.isArray(history)
        && history.some((r) => RELEASE_ACTIVE_STATES.has(r.state)) ? 5000 : false;
    },
  });

export interface SubmitReleasesBody { items: { component: string; tag: string }[] }

export const useSubmitReleases = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: SubmitReleasesBody) => apiSend("POST", "/api/admin/releases", b),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["releases"] });
      qc.invalidateQueries({ queryKey: ["release-targets"] });
    },
  });
};
```

- [ ] **Step 5: 화면을 만든다**

`frontend/src/features/releases/ReleasesPage.tsx` — Step 2의 테스트가 계약을 전부 고정한다. 뼈대:

```tsx
import { useState } from "react";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { Table } from "../../components/ui/Table";
import { ApiError, reasonText } from "../../lib/api";
import type { Release, ReleaseTarget } from "../../lib/types";
import { RELEASE_ACTIVE_STATES, useReleases, useReleaseTargets,
         useSubmitReleases } from "./useReleases";

export function ReleasesPage() {
  const targetsQ = useReleaseTargets();
  const releasesQ = useReleases();
  const submit = useSubmitReleases();
  const [picks, setPicks] = useState<Record<string, string>>({});

  const targets: ReleaseTarget[] =
    Array.isArray(targetsQ.data?.targets) ? targetsQ.data.targets : [];
  const history: Release[] =
    Array.isArray(releasesQ.data?.history) ? releasesQ.data.history : [];
  const current = releasesQ.data?.current ?? {};
  const active = history.some((r) => RELEASE_ACTIVE_STATES.has(r.state));
  const items = Object.entries(picks)
    .filter(([, tag]) => tag !== "")
    .map(([component, tag]) => ({ component, tag }));
  // ...
}
```

렌더 요구(테스트에 맞춘다):
- `<h1 className="text-lg font-semibold">릴리스</h1>` + `{active && <span>진행 중</span>}` 배지.
- 경고 문단(항상): `컨트롤러를 갱신하면 컨트롤러가 재시작되어 롤아웃 추적이 잠시 끊깁니다 — 화면이 멈춘 것은 장애가 아닙니다.`
- `{targetsQ.data && targetsQ.data.registry_ok === false && <p className="text-bad">{reasonText("registry_unreachable")}</p>}`
- 대상 표: 행마다 `component` / 현재 이미지 `t.current_image ?? ((t.current_images ?? []).length ? t.current_images.join(", ") : "—")` / `<select aria-label={t.component}>`(첫 옵션 `<option value="">변경 없음</option>`, 이후 `(t.tags ?? []).map(...)`) / 상태(`current[t.component]` 있으면 `state` + `reasonText(reason_code)`, 없으면 `—`).
- 제출: `<Button onClick={() => submit.mutate({ items })} disabled={items.length === 0 || submit.isPending}>롤아웃 시작</Button>`; `submit.isError`면 `{(submit.error as ApiError).message}`를 `text-bad`로.
- 이력 표: `history.map` — 시각(`applied_at`) / 컴포넌트 / 태그 / 상태 / `reasonText(r.reason_code)` / actor. null은 `—`.
- 로딩은 `불러오는 중…`, `targetsQ.isError`/`releasesQ.isError`는 `(q.error as ApiError).message`.

- [ ] **Step 6: 라우트와 내비를 등록한다**

`frontend/src/app/router.tsx` — import와 함께 `/admin/builds` 라우트 아래에:

```tsx
          <Route path="/admin/releases" element={<RequireRole role="admin"><AppShell><ReleasesPage /></AppShell></RequireRole>} />
```

`frontend/src/app/AppShell.tsx` — 빌드 링크 아래에:

```tsx
        {isAdmin && <NavLink to="/admin/releases" className={linkCls}>릴리스</NavLink>}
```

- [ ] **Step 7: 통과를 확인한다**

Run: `cd frontend && npx vitest run` 그리고 `npx tsc -b`
Expected: 전체 PASS, 타입 에러 0 (기존 라우터 테스트가 내비 링크 수를 단언하고 있으면 그 기대값을 갱신한다 — 단언 삭제 금지)

- [ ] **Step 8: 커밋**

```bash
git add frontend/src
git commit -m "feat(portal): 릴리스 화면 -- 태그 선택과 롤아웃 진행 표시"
```

---
### Task 7: RBAC + 배포 설정 + 문서

**Files:**
- Modify: `deploy/k8s/10-rbac.yaml`
- Modify: `deploy/k8s/20-config.yaml`
- Modify: `deploy/README.md`

**Interfaces:**
- Consumes: Task 3의 설정 이름(`DMS_ROLLOUT_INTERVAL_SECONDS`/`DMS_ROLLOUT_TIMEOUT_SECONDS`), Task 4의 롤아웃 동작.
- Produces: 롤아웃 가능한 매니페스트와 운영 절차 문서.

- [ ] **Step 1: 컨트롤러 Role에만 apps 권한을 더한다**

`deploy/k8s/10-rbac.yaml`의 **dms-controller Role** `rules` 끝(`batch.volcano.sh` `jobs/status` 규칙 뒤)에 더한다. **api Role은 건드리지 않는다** — 의도적으로 create-free이고 patch도 없다, 롤아웃은 컨트롤러가 한다(설계 §5). 주석은 이 파일의 기존 영어 톤을 따른다:

```yaml
  # Slice 13 (portal-driven rollout): RolloutWatcher (rollout_watcher.py) patches
  # the image of the three DMS workloads and reads their status/generation to
  # decide convergence (execution_volcano.KubernetesClient.patch_workload/
  # get_workload). resourceNames-scoped -- the controller has no business
  # patching arbitrary workloads in the namespace.
  - apiGroups: ["apps"]
    resources: ["deployments", "daemonsets"]
    resourceNames: ["dms-api", "dms-controller", "dms-agent"]
    verbs: ["get", "patch"]
  # resourceNames does not apply to list -- grant it separately, read-only.
  - apiGroups: ["apps"]
    resources: ["deployments", "daemonsets"]
    verbs: ["list"]
  # status is a SEPARATE RBAC resource (same convention as pods/status above).
  - apiGroups: ["apps"]
    resources: ["deployments/status", "daemonsets/status"]
    verbs: ["get"]
```

- [ ] **Step 2: ConfigMap에 롤아웃 설정을 더한다**

`deploy/k8s/20-config.yaml`의 슬라이스 11 블록 아래에:

```yaml
  # --- 포탈 주도 롤아웃 (슬라이스 13) ---
  # 간격 10초 -> per-loop 리스 30초. 리스는 갱신되지 않으므로 간격을 늘리면
  # 컨트롤러 자기 갱신 뒤 새 파드의 리스 재획득이 그만큼 늦어져 롤아웃 추적이
  # 몇 배로 멈춘다(설계 §2) -- 늘리기 전에 그 트레이드오프를 확인할 것.
  DMS_ROLLOUT_INTERVAL_SECONDS: "10"
  # DaemonSet 수렴의 벽시계 상한(초). DaemonSet에는 progressDeadlineSeconds가
  # 없어 이것이 유일한 실패 수단이다. Deployment는 PDE(600)가 확정하고, 이 값의
  # 3배가 최후 회수로만 쓰인다(rollout_watcher.py).
  DMS_ROLLOUT_TIMEOUT_SECONDS: "600"
```

- [ ] **Step 3: RBAC 반영을 확인한다 (테스트베드가 있을 때)**

Run: `kubectl --context dms apply -f deploy/k8s/10-rbac.yaml && kubectl --context dms auth can-i patch deployments.apps/dms-controller --as=system:serviceaccount:dms:dms-controller -n dms`
Expected: `yes`. 그리고 `kubectl --context dms auth can-i patch deployments.apps --as=system:serviceaccount:dms:dms-api -n dms` → `no` (api는 여전히 patch 불가).
클러스터가 없으면 이 단계는 건너뛰고 보고서에 "미검증"으로 남긴다.

- [ ] **Step 4: README에 절차와 제약을 적는다**

`deploy/README.md`의 「8. 포탈에서 이미지 빌드」 다음에 「9. 포탈에서 릴리스(롤아웃) (슬라이스 13)」 절을 더한다. 반드시 담을 것:

- 흐름: 「빌드」로 태그 생성 → 「릴리스」에서 컴포넌트별 태그 선택 → 한 배치 제출 → 컨트롤러가 `dms-agent` → `dms-api` → `dms-controller` 순서로 patch → 화면에서 수렴 확인.
- **컨트롤러 자기 갱신**: `dms-controller`가 배치의 마지막이며, 패치되는 순간 컨트롤러 파드가 재시작되어 「릴리스」 화면 갱신이 리스 재획득(최대 ~30초 + 파드 기동)만큼 멈춘다 — 장애가 아니다. 새 파드가 `Applying` 행을 이어받아 `Applied`로 수렴시킨다.
- **같은 태그 재적용은 거부된다(`same_tag`)** — 모든 매니페스트가 `imagePullPolicy: IfNotPresent`라 아무 일도 일어나지 않기 때문.
- **정적 YAML이 선언적 진실이다(설계 §9)**: 롤아웃 성공 후 `40-api.yaml`/`41-controller.yaml`/`30-migrate-job.yaml`(dms 계보)과 `50-agent-daemonset.yaml`(dms-agent 계보)의 `image:` 태그를 **손으로** 맞춰야 한다. 안 맞추면 다음 `kubectl apply`가 옛 태그로 되돌린다. 이 슬라이스는 파일을 자동으로 고치지 않으며(컨트롤러 파드에 저장소가 없다), 어긋남 표시는 슬라이스 14 대시보드의 몫이다.
- Helm/kustomize를 도입하지 않는다 — 이 README에 기록된 설계 결정이다.
- `DMS_JOB_IMAGE`(ConfigMap)는 롤아웃 대상이 아니다 — 이미지 패치가 아니라 ConfigMap 갱신 + 소비자 재시작이 필요해서 범위 밖(설계 §10).
- 존재하지 않는 태그를 강제로 넣으면(레지스트리 다운 시 검증이 fail-open) `ImagePullBackOff` 후 타임아웃/`ProgressDeadlineExceeded`로 `Failed`가 된다.

- [ ] **Step 5: 실증 체크리스트를 문서에 남긴다**

같은 절 끝에 설계 §11의 8개 실증 항목을 체크리스트로 옮겨 적는다 (플랜 실행 후 테스트베드에서 별도 수행) — 특히 6번(**컨트롤러 자기 갱신**: 컨트롤러가 죽은 뒤 새 파드가 `Applying` 행을 이어받아 `Applied`로 수렴)이 이 슬라이스의 핵심 실증이라고 명시한다.

- [ ] **Step 6: 커밋**

```bash
git add deploy/
git commit -m "deploy: 롤아웃 RBAC(컨트롤러 한정)과 설정, 릴리스 절차 문서화"
```

---

## Self-Review

**1. 스펙 커버리지**

| 설계 절 | 담당 태스크 |
|---|---|
| §1 실측 전제(컨테이너 이름·태그 계보·라벨) | Task 1 `COMPONENTS` (테스트로 고정) |
| §2 2단계 record-then-patch, 순서 agent→api→controller, 순서의 DB 지속(seq), 재패치 멱등, 루프 간격 10–15초 | Task 1(순서 지속) + Task 4(record-then-patch·재패치·회수) + Task 3(간격 10초 설정) |
| §3 Deployment 판정(세대 게이트→PDE→3조건, ReplicaFailure 노출), DaemonSet 판정(4조건)·벽시계·멈춘 노드 사유 | Task 2(순수 함수) + Task 4(벽시계·pod_briefs 사유) |
| §4 새 좁은 Protocol, `_ensure` 본문 확장, strategic merge + `_content_type` 명시, 키 정규화, 404/403 | Task 3 |
| §5 RBAC — 컨트롤러 Role에만, resourceNames 한정, list 별도, `*/status` | Task 7 |
| §6 데이터 모델 — `reason_code`/`seq` 추가(`_ensure_columns` 양쪽), 상태기계, MAX(id) 현재 | Task 1 |
| §7 API 3종, 거부 5종, 레지스트리 실패 내성(fail-open) | Task 5 |
| §8 포탈 화면 — h1 릴리스, 3행+select, 한 배치 제출, 폴링, 이력, reasonText, 컨트롤러 경고 | Task 6 |
| §9 매니페스트 수동 동기화 문서화, Helm/kustomize 금지 | Task 7 |
| §10 범위 밖 4종 | Global Constraints에 명시, 어느 태스크도 침범 안 함 |
| §11 실증 8종 | Task 7 Step 5에 체크리스트로 남김 — 플랜 실행 후 테스트베드에서 수행 |

**2. 플레이스홀더 점검** — "적절히 처리한다"/"TBD"/코드 없는 "테스트를 작성한다" 없음. 코드 단계마다 실제 코드가 있다. Task 6 Step 5만 뼈대+산문인데, 그 화면의 계약(h1, 경고 문구, aria-label, 버튼 이름, 정규화, 폴링 조건, 번역)은 Step 2의 테스트가 전부 고정해 구현이 결정된다. Task 7 Step 4·5는 문서 작성이라 담을 항목을 전부 열거했다.

**3. 타입 일관성** — `ROLLOUT_ORDER`/`COMPONENTS`(키: `kind`/`workload`/`container`/`repository`/`selector`)는 Task 1이 정의하고 4·5가 같은 키로 쓴다. `ReleasesRepository` 메서드명(`create_batch`/`active`/`mark_applying`/`finish`/`abort_pending`/`current`/`list`)은 Task 1 정의를 4·5가 그대로 쓴다. 정규화 dict 키(`observed_generation`/`updated_replicas`/`desired_number_scheduled`/`images` 등)는 Task 2가 정의하고 Task 3의 `get_workload` 반환·Stub·Task 4의 fake가 같은 키를 쓴다. `RolloutRunner.patch_image/observe/pod_briefs` 키워드 시그니처는 Task 3 정의를 Task 4·5의 fake가 동일하게 구현한다. `pod_briefs` 항목 키(`name`/`node`/`images`/`phase`/`waiting_reason`)는 3·4·5에서 동일. 프론트 `Release`/`ReleaseTarget` 필드는 Task 5 응답 dict와 1:1. 사유 코드는 도입 태스크(1: `rollout_in_progress`, 3: `patch_failed`/`observe_failed`, 4: `rollout_failed`/`rollout_timeout`/`rollout_aborted`/`workload_not_found`, 5: `unknown_component`/`unknown_tag`/`same_tag`, 6: `registry_unreachable`)에서 json+매핑 양쪽에 넣는다.

**4. 설계와의 긴장 — 플랜이 내린 결정 (리뷰어 확인 요망)**

1. **targets의 "클러스터 현재 이미지" 출처**: 설계 §7은 api가 현재 이미지를 주라 하고, §5는 apps RBAC을 컨트롤러에만 부여한다 — api는 apps `get`이 없어 Deployment spec을 못 읽는다. 해소: api가 **이미 가진 `pods list` 권한**으로 라벨 셀렉터 조회(`PodBriefReader.list_pod_briefs`)를 해 실행 중 파드의 이미지에서 유도한다. §5를 그대로 지키는 유일한 경로다. 부작용: 롤링 중에는 이미지가 2종일 수 있어 `current_image`(단일일 때만)와 `current_images`(전체)를 분리했고, `same_tag` 검사는 단일일 때만 강제된다(fail-open — 레지스트리 검증과 같은 원칙).
2. **`WorkloadClient` 두 메서드 유지 + `PodBriefReader` 별도 Protocol**: 설계 §4는 "두 메서드"라 했다. 파드 읽기(§3 멈춘 노드 사유 + 위 1번)를 그 Protocol에 넣으면 세 메서드가 되므로, `WorkloadClient`는 두 메서드로 유지하고 별도 좁은 Protocol을 추가했다.
3. **Deployment 최후 회수 `timeout*3`**: 설계 §3은 Deployment에 "자체적으로 더 짧은 상한을 두지 않는다"고 했다. 그러나 상태 조회가 지속 실패하면(RBAC 오설정 등) `Applying`이 영원히 남아 `rollout_in_progress`가 새 롤아웃을 영구 차단한다. PDE(진행 시 리셋)보다 확실히 긴 3배 벽시계를 최후 회수로만 둔다 — "더 짧은 상한"이 아니므로 충돌하지 않는다고 판단했다.
4. **실패 시 배치 중단(`rollout_aborted`)**: 설계는 한 컴포넌트 실패 후 뒤 컴포넌트의 처리를 명시하지 않았다. 진행하면 반쯤 섞인 버전 조합이 생기고, 방치하면 배치가 영원히 활성이다 — 남은 `Pending`을 `Failed`/`rollout_aborted`로 닫는 쪽을 골랐다(이력에서 옛 태그를 다시 골라 재시도하면 된다는 §10 롤백 논리와 일관).
5. **`ApiException` 타입 대신 `status` 속성 판별**: `.venv`에 `kubernetes`가 없어 테스트가 그 타입을 만들 수 없다. `getattr(exc, "status", None)`으로 404/403을 판별한다 — 시맨틱은 동일하고("404→None, 그 외 재전파") 테스트 가능해진다.

**5. 알려진 위험**

- **strategic merge patch의 `_content_type` 인자**: kubernetes 파이썬 클라이언트 버전에 따라 `_content_type` kwarg 지원이 다르다(구버전은 header_params를 직접 노출하지 않음). 실증(§11.4~6)에서 patch가 415/400을 내면 대응은 `api_client.call_api`를 쓰거나 클라이언트 버전을 올리는 것이다 — 단위 테스트는 kwarg 전달만 고정하므로 이 위험은 실증에서만 드러난다.
- **`same_tag`의 파드 기반 판정**: Deployment spec이 아니라 실행 중 파드 이미지 기준이라, 방금 패치돼 아직 옛 파드만 도는 순간에는 새 태그==spec 태그 제출이 `same_tag`로 안 걸릴 수 있다. 그 경우에도 재패치는 no-op(멱등)이라 실해는 없다.
- **DaemonSet 타임아웃 600초**: 5노드 순차 롤링(이미지 pull 포함)이 정상적으로 600초를 넘기면 거짓 실패가 난다. 그 경우 `DMS_ROLLOUT_TIMEOUT_SECONDS`를 올리면 된다 — 실증 §11.4에서 실측할 것.
- **레지스트리 조회가 요청 경로에서 동기 호출**: targets/POST가 최대 2회(리포 2종) × 3초 타임아웃을 문다. 폴링 화면은 targets를 폴링하지 않고(releases만 폴링) 최초 로드에만 부르므로 수용했다.

