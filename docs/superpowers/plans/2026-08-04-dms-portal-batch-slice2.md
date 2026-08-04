# DMS 포탈 슬라이스 2 (배치성 대량 묶음) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** scan·sync 대량 묶음(batch) 기능 — 부모 batch + 자식 batch_items(CSV intake)를 운영자 지정 동시성 상한으로 쓰로틀 materialize하고, sync는 배치 단위 preview→confirm 게이트를 거쳐 실행, 집계 종료 + 실패분 재실행하며, 포탈 배치 페이지 3화면으로 운영한다.

**Architecture:** 자식(batch_items)은 기존 request→plan→run 파이프라인을 그대로 재사용한다(planner/stepper 무변경). 새 `BatchOrchestrator` 컨트롤러 루프가 batch_items를 실제 requests로 온디맨드 materialize(동시성 쓰로틀), 자식 상태를 집계, sync 배치는 자식 job을 내부 confirm한다. 포탈은 슬라이스 1의 C 디자인·컴포넌트·api 클라이언트를 재사용한다.

**Tech Stack:** Python 3.11 / FastAPI / pytest (백엔드), React+Vite+TS / TanStack Query / Radix / Vitest+MSW (프론트, 슬라이스 1과 동일).

## Global Constraints

- 배치 상태: `Previewing, PreviewReady, Running, Completed, Cancelled`. batch_item 상태: `Queued, Materialized, Succeeded, Failed, Rejected, Cancelled`.
- 배치 대상 operation = **scan, sync** 만(rm 제외). 생성 시 초기 상태: scan→`Running`, sync→`Previewing`.
- 실행 = 운영자 지정 `max_concurrency`(≥1) 쓰로틀, 자식 온디맨드 materialize(batch_items 원장). 자식 priority는 `"mid"` 고정.
- sync = 자식 전원 preview(ConfirmPending) 후 배치 `PreviewReady` → 운영자 배치 confirm → 실행(쓰로틀). 자식 confirm 내부 호출 = `data_jobs.set_confirmed(job_id, fingerprint)` + `data_jobs.set_job_state(job_id, DataJobState.EXECUTING, actor=...)`.
- 실패 = 집계 종료(succeeded/failed counts) + 실패/거부 자식 **수동 재실행**(Queued로 리셋, request_id 비움).
- 자식은 평범한 `Pending` 요청(`requests.create(..., batch_id=)`). planner/stepper/placement 무변경. `requests.batch_id` NULL = 일회성(슬라이스 1) 요청.
- 모든 거부/실패엔 reason_code. API는 `require_admin`(운영자). 세션 인증은 슬라이스 1 재사용.
- 백엔드 테스트: `.venv/bin/pytest`(시스템 pytest 아님). 프론트: `cd frontend && npm test` / `npx tsc -b`. 기존 전체 스위트(백엔드 327 + 프론트 23) green 유지.
- DB 이중 방언: `Database.dialect` = `"sqlite"`/`"postgresql"`. 신규 JSON 컬럼/타임스탬프는 `db.py`의 `dump_json`/`load_json`/`utc_now_iso`/`iso_plus` 사용.
- 커밋 trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

## File Structure

```
src/dms/
  domain.py                     # (수정) build_data_payload + validate_batch 추가
  migrations.py                 # (수정) batches/batch_items 테이블 + requests.batch_id
  config.py                     # (수정) batch_orchestrator_interval_seconds
  batch_orchestrator.py         # (신규) BatchOrchestrator 루프
  controller.py                 # (수정) batch-orchestrator Loop 등록
  repositories/
    __init__.py                 # (수정) self.batches
    requests.py                 # (수정) create(..., batch_id=None)
    batches.py                  # (신규) BatchesRepository
  api/
    routes_batches.py           # (신규) /api/admin/batches ...
    app.py                      # (수정) batches_router include
frontend/src/
  lib/types.ts                  # (수정) Batch, BatchItem
  lib/csv.ts                    # (신규) parseBatchCsv 순수함수
  features/batches/
    useBatches.ts               # (신규) 훅
    BatchesList.tsx  BatchCreate.tsx  BatchDetail.tsx   # (신규) 3화면
  app/AppShell.tsx              # (수정) "배치 작업" 내비 활성
  app/router.tsx                # (수정) /admin/batches 3라우트
```

실행 순서 = 1→2→3→4→5→6→7→8→9→10→11 (백엔드 1-7 먼저, 프론트 8-10 화면 후 11 내비/라우트가 화면 import).

---

## Task 1: 도메인 — build_data_payload + validate_batch

자식 요청 payload/resource_key를 만드는 정규 함수와 배치 검증을 추가하고, 기존 `routes_requests._validated_payload`를 새 함수로 리팩터해 중복을 없앤다.

**Files:**
- Modify: `src/dms/domain.py`, `src/dms/api/routes_requests.py`
- Test: `tests/test_domain_batch.py`

**Interfaces:**
- Produces:
  - `build_data_payload(operation: str, *, storage=None, target=None, source_storage=None, source=None, destination_storage=None, destination=None, options: dict) -> tuple[dict, str]` — (payload, resource_key). scan/rm: `{"storage","target"}`; sync: `{"source_storage","source","destination_storage","destination"}`. 경로/옵션 검증 후 fingerprint+resource_key 계산.
  - `validate_batch(operation: str, max_concurrency: int, items: list[dict]) -> None` — operation∈{scan,sync}, max_concurrency≥1, items 비어있지 않음. 위반 시 `DomainValidationError`.

- [ ] **Step 1: 실패 테스트 — `tests/test_domain_batch.py`**

```python
import pytest
from dms.domain import build_data_payload, validate_batch, DomainValidationError

def test_build_data_payload_scan():
    # payload INCLUDES the validated options (single source of the full request payload)
    payload, key = build_data_payload("scan", storage="s1", target="a/b", options={})
    assert payload == {"storage": "s1", "target": "a/b", "options": {}}
    assert key.startswith("data.scan:s1:a/b:")

def test_build_data_payload_sync():
    payload, key = build_data_payload("sync", source_storage="s1", source="a",
        destination_storage="s2", destination="b", options={"delete": True})
    assert payload == {"source_storage": "s1", "source": "a",
                       "destination_storage": "s2", "destination": "b",
                       "options": {"delete": True}}
    assert key.startswith("data.sync:s1:a:s2:b:")

def test_build_data_payload_missing_storage():
    with pytest.raises(DomainValidationError):
        build_data_payload("scan", target="a", options={})   # storage 누락 → 방어 가드

def test_build_data_payload_rejects_bad_path():
    with pytest.raises(DomainValidationError):
        build_data_payload("scan", storage="s1", target="../escape", options={})

def test_validate_batch_ok():
    validate_batch("scan", 3, [{"storage": "s1", "target": "a"}])

def test_validate_batch_rejects():
    with pytest.raises(DomainValidationError):
        validate_batch("rm", 3, [{}])            # rm 불가
    with pytest.raises(DomainValidationError):
        validate_batch("scan", 0, [{}])          # max_concurrency<1
    with pytest.raises(DomainValidationError):
        validate_batch("scan", 3, [])            # empty
```

- [ ] **Step 2: 실패 확인** — Run: `.venv/bin/pytest tests/test_domain_batch.py -v` → FAIL(함수 없음).

- [ ] **Step 3: 구현 — `domain.py`에 추가**

```python
def build_data_payload(operation, *, storage=None, target=None, source_storage=None,
                       source=None, destination_storage=None, destination=None,
                       options: dict) -> tuple[dict, str]:
    op = Operation(operation)
    opts = validate_options(op, options)
    fp = option_fingerprint(opts)
    if op is Operation.SYNC:
        src, dst = validate_sync_paths(source or "", destination or "")
        if not source_storage or not destination_storage:
            raise DomainValidationError("missing_storage")
        payload = {"source_storage": source_storage, "source": src,
                   "destination_storage": destination_storage, "destination": dst,
                   "options": opts}
        key = build_resource_key(op, source_storage=source_storage, source=src,
                                 destination_storage=destination_storage,
                                 destination=dst, fingerprint=fp)
        return payload, key
    if op is Operation.RM:
        if not storage:
            raise DomainValidationError("missing_storage")
        tgt = validate_rm_target(target or "", opts)
        return ({"storage": storage, "target": tgt, "options": opts},
                build_resource_key(op, storage=storage, target=tgt, fingerprint=fp))
    # scan
    if not storage:
        raise DomainValidationError("missing_storage")
    tgt = validate_relative_path(target or "")
    return ({"storage": storage, "target": tgt, "options": opts},
            build_resource_key(op, storage=storage, target=tgt, fingerprint=fp))
```

**payload에 `options`가 포함된다** — 이것이 요청 payload의 단일 출처다. `routes_requests`와 배치
오케스트레이터(Task 4) 둘 다 이 payload를 그대로 requests.create에 넘긴다(오케스트레이터는
options 유실 없이 자식 요청을 만든다). 아래 Step 4 리팩터는 이 payload를 쓰고 owner_username만 더한다.

```python
def validate_batch(operation, max_concurrency, items) -> None:
    if operation not in (Operation.SCAN.value, Operation.SYNC.value):
        raise DomainValidationError("invalid_batch_operation", operation)
    if not isinstance(max_concurrency, int) or isinstance(max_concurrency, bool) or max_concurrency < 1:
        raise DomainValidationError("invalid_max_concurrency")
    if not items:
        raise DomainValidationError("empty_batch")
```

- [ ] **Step 4: `routes_requests._validated_payload` 리팩터(중복 제거)**

`_validated_payload(body)`의 operation별 payload/resource_key 계산부를 `build_data_payload`
호출로 교체한다. `build_data_payload`가 이제 **payload에 검증된 options를 포함**하므로, 상단의
별도 `validate_options(...)` 선호출과 `payload.update({"options": ...})`는 제거하고, 반환된 payload에
`owner_username`만 더한다(옵션 검증은 build_data_payload가 단일 수행 = 이중 검증 제거). 예: sync 분기를
```python
    if op is Operation.SYNC:
        _require(body.source_storage, "missing_source_storage")   # 기존 구체 사유코드 유지
        _require(body.destination_storage, "missing_destination_storage")
        payload, key = build_data_payload("sync", source_storage=body.source_storage,
            source=body.source, destination_storage=body.destination_storage,
            destination=body.destination, options=body.options)
```
처럼. scan/rm도 동일하게(scan/rm은 `_require(body.storage, "missing_storage")` 유지). 이후
`payload["owner_username"] = body.owner_username`만 추가. `option_fingerprint`/`build_resource_key`
직접 호출과 상단 `validate_options` 선호출은 제거. **불변 유지**: priority 체크, `validate_owner_username`,
`submit()`의 특권-owner 게이트, 그리고 `missing_source_storage`/`missing_destination_storage` 등 구체
사유코드(기존 `test_api_requests.py` green). `build_data_payload`가 예상 밖 키에서 던지는 것은 그대로 422.

- [ ] **Step 5: 통과 + 회귀** — Run: `.venv/bin/pytest tests/test_domain_batch.py tests/test_api_requests.py tests/test_domain_options.py tests/test_domain_paths.py -v` → 신규 PASS + 기존 요청 테스트 그대로 통과. 이어 `.venv/bin/pytest -q`.

- [ ] **Step 6: 커밋**
```bash
git add src/dms/domain.py src/dms/api/routes_requests.py tests/test_domain_batch.py
git commit -m "feat(domain): build_data_payload + validate_batch; dedup request payload build"
```

---

## Task 2: 마이그레이션 — batches / batch_items / requests.batch_id

**Files:**
- Modify: `src/dms/migrations.py`
- Test: `tests/test_migrations_batch.py`

**Interfaces:**
- Produces: 테이블 `batches`, `batch_items`; `requests.batch_id TEXT NULL`. 신규 DB는 CREATE TABLE로,
  구형 DB는 `_ensure_columns`의 ALTER로 `requests.batch_id` 보강.

- [ ] **Step 1: 실패 테스트 — `tests/test_migrations_batch.py`**

```python
from dms.db import Database
from dms.migrations import migrate

def _cols(db, table):
    if db.dialect == "sqlite":
        return {r["name"] for r in db.query(f"PRAGMA table_info({table})")}
    return {r["column_name"] for r in db.query(
        "SELECT column_name FROM information_schema.columns WHERE table_name = :t", {"t": table})}

def test_batch_tables_exist(tmp_path):
    db = Database.connect(f"sqlite:///{tmp_path}/m.db"); migrate(db)
    assert {"batch_id","operation","status","max_concurrency","options","note",
            "item_count","succeeded_count","failed_count"} <= _cols(db, "batches")
    assert {"batch_id","seq","payload","status","request_id","reason_code"} <= _cols(db, "batch_items")

def test_requests_has_batch_id(tmp_path):
    db = Database.connect(f"sqlite:///{tmp_path}/m2.db"); migrate(db)
    assert "batch_id" in _cols(db, "requests")

def test_batch_id_backfilled_on_old_db(tmp_path):
    db = Database.connect(f"sqlite:///{tmp_path}/m3.db")
    # 구형: requests를 batch_id 없이 만든 뒤 migrate가 ALTER로 보강하는지
    db.execute("""CREATE TABLE requests (request_id TEXT PRIMARY KEY, commit_order INTEGER,
        operation TEXT, requester_id TEXT, actor TEXT, resource_key TEXT, priority TEXT,
        payload TEXT, state TEXT, created_at TEXT, updated_at TEXT)""")
    migrate(db)
    assert "batch_id" in _cols(db, "requests")
```

- [ ] **Step 2: 실패 확인** — Run: `.venv/bin/pytest tests/test_migrations_batch.py -v` → FAIL.

- [ ] **Step 3: 구현 — `migrations.py`의 `stmts` 리스트에 CREATE TABLE 추가**

`requests` CREATE TABLE의 컬럼 목록에 `batch_id TEXT` 한 줄 추가(예: `updated_at TEXT NOT NULL` 뒤에 `, batch_id TEXT)`). 그리고 `stmts` 리스트(다른 CREATE TABLE들 옆)에 추가:

```python
        """CREATE TABLE IF NOT EXISTS batches (
            batch_id TEXT PRIMARY KEY,
            operation TEXT NOT NULL,
            requester_id TEXT NOT NULL,
            actor TEXT NOT NULL,
            status TEXT NOT NULL,
            max_concurrency INTEGER NOT NULL,
            options TEXT NOT NULL,
            note TEXT,
            item_count INTEGER NOT NULL DEFAULT 0,
            succeeded_count INTEGER NOT NULL DEFAULT 0,
            failed_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL)""",
        "CREATE INDEX IF NOT EXISTS idx_batches_status ON batches (status, created_at)",
        """CREATE TABLE IF NOT EXISTS batch_items (
            batch_id TEXT NOT NULL,
            seq INTEGER NOT NULL,
            payload TEXT NOT NULL,
            status TEXT NOT NULL,
            request_id TEXT,
            reason_code TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (batch_id, seq))""",
        "CREATE INDEX IF NOT EXISTS idx_batch_items_status ON batch_items (batch_id, status)",
        "CREATE INDEX IF NOT EXISTS idx_requests_batch ON requests (batch_id)",
```

`_ensure_columns`의 튜플에 `("requests", "batch_id", "TEXT")` 추가.

- [ ] **Step 4: 통과 + 회귀** — Run: `.venv/bin/pytest tests/test_migrations_batch.py tests/test_migrations.py -v && .venv/bin/pytest -q`.

- [ ] **Step 5: 커밋**
```bash
git add src/dms/migrations.py tests/test_migrations_batch.py
git commit -m "feat(db): batches/batch_items tables + requests.batch_id"
```

---

## Task 3: BatchesRepository + requests.create(batch_id)

**Files:**
- Create: `src/dms/repositories/batches.py`
- Modify: `src/dms/repositories/__init__.py`, `src/dms/repositories/requests.py`
- Test: `tests/test_repo_batches.py`

**Interfaces:**
- Consumes: `Database`(dump_json/load_json/utc_now_iso).
- Produces `BatchesRepository`:
  - `create(*, operation, requester_id, actor, max_concurrency, options: dict, note, items: list[dict], status: str) -> str` (batch_id hex). batch + N batch_items(seq 0..N-1, status="Queued", payload=item). item_count=len(items).
  - `get(batch_id) -> dict | None` (options JSON 하이드레이트).
  - `list(limit=100) -> list[dict]`.
  - `list_active() -> list[dict]` (status∈{Previewing,Running}).
  - `list_items(batch_id) -> list[dict]` (payload 하이드레이트, seq 오름차순).
  - `set_item_materialized(batch_id, seq, request_id)`.
  - `set_item_status(batch_id, seq, status, *, reason_code=None)`.
  - `reset_item_to_queued(batch_id, seq)` (status="Queued", request_id=NULL, reason_code=NULL).
  - `set_status(batch_id, status)`.
  - `bump_counts(batch_id, *, succeeded=0, failed=0)`.
  - `reset_failed_items(batch_id) -> int` (status∈{Failed,Rejected} → Queued, request_id=NULL; 반환=리셋 수; failed_count 차감).
- `requests.create`에 `batch_id=None` 키워드 추가(INSERT 컬럼/값 포함).

- [ ] **Step 1: 실패 테스트 — `tests/test_repo_batches.py`**

```python
from dms.repositories import Repositories

def test_create_and_get(db):
    repos = Repositories(db)
    bid = repos.batches.create(operation="scan", requester_id="admin", actor="admin",
        max_concurrency=2, options={}, note="n",
        items=[{"storage":"s1","target":"a"},{"storage":"s1","target":"b"}], status="Running")
    b = repos.batches.get(bid)
    assert b["operation"]=="scan" and b["status"]=="Running" and b["item_count"]==2
    items = repos.batches.list_items(bid)
    assert [it["seq"] for it in items]==[0,1]
    assert all(it["status"]=="Queued" for it in items)
    assert items[0]["payload"]=={"storage":"s1","target":"a"}

def test_materialize_and_counts(db):
    repos = Repositories(db)
    bid = repos.batches.create(operation="scan", requester_id="admin", actor="admin",
        max_concurrency=2, options={}, note=None,
        items=[{"storage":"s1","target":"a"}], status="Running")
    repos.batches.set_item_materialized(bid, 0, "req-1")
    assert repos.batches.list_items(bid)[0]["request_id"]=="req-1"
    repos.batches.set_item_status(bid, 0, "Succeeded")
    repos.batches.bump_counts(bid, succeeded=1)
    assert repos.batches.get(bid)["succeeded_count"]==1

def test_reset_failed(db):
    repos = Repositories(db)
    bid = repos.batches.create(operation="scan", requester_id="admin", actor="admin",
        max_concurrency=2, options={}, note=None,
        items=[{"storage":"s1","target":"a"}], status="Completed")
    repos.batches.set_item_status(bid, 0, "Failed", reason_code="x")
    repos.batches.bump_counts(bid, failed=1)
    n = repos.batches.reset_failed_items(bid)
    assert n==1 and repos.batches.list_items(bid)[0]["status"]=="Queued"
    assert repos.batches.get(bid)["failed_count"]==0

def test_active_filter(db):
    repos = Repositories(db)
    a = repos.batches.create(operation="scan", requester_id="x", actor="x", max_concurrency=1,
        options={}, note=None, items=[{"storage":"s","target":"a"}], status="Running")
    repos.batches.create(operation="scan", requester_id="x", actor="x", max_concurrency=1,
        options={}, note=None, items=[{"storage":"s","target":"b"}], status="Completed")
    assert [b["batch_id"] for b in repos.batches.list_active()]==[a]

def test_requests_create_with_batch_id(db):
    repos = Repositories(db)
    rid = repos.requests.create(operation="scan", requester_id="admin", actor="admin",
        resource_key="k", payload={"storage":"s","target":"a"}, priority="mid", batch_id="b1")
    assert repos.requests.get(rid)["batch_id"]=="b1"
```

- [ ] **Step 2: 실패 확인** — Run: `.venv/bin/pytest tests/test_repo_batches.py -v` → FAIL.

- [ ] **Step 3: 구현 — `src/dms/repositories/batches.py`**

```python
import uuid
from ..db import Database, dump_json, load_json, utc_now_iso

_ACTIVE = ("Previewing", "Running")

class BatchesRepository:
    def __init__(self, db: Database):
        self._db = db

    def create(self, *, operation, requester_id, actor, max_concurrency, options,
               note, items, status) -> str:
        bid = uuid.uuid4().hex
        now = utc_now_iso()
        with self._db.transaction():
            self._db.execute(
                """INSERT INTO batches (batch_id, operation, requester_id, actor, status,
                       max_concurrency, options, note, item_count, succeeded_count,
                       failed_count, created_at, updated_at)
                   VALUES (:id,:op,:req,:actor,:st,:mc,:opt,:note,:n,0,0,:now,:now)""",
                {"id": bid, "op": operation, "req": requester_id, "actor": actor,
                 "st": status, "mc": max_concurrency, "opt": dump_json(options),
                 "note": note, "n": len(items), "now": now})
            for seq, item in enumerate(items):
                self._db.execute(
                    """INSERT INTO batch_items (batch_id, seq, payload, status, request_id,
                           reason_code, created_at, updated_at)
                       VALUES (:b,:s,:p,'Queued',NULL,NULL,:now,:now)""",
                    {"b": bid, "s": seq, "p": dump_json(item), "now": now})
        return bid

    def get(self, batch_id):
        row = self._db.query_one("SELECT * FROM batches WHERE batch_id = :b", {"b": batch_id})
        if row is not None:
            row["options"] = load_json(row["options"])
        return row

    def list(self, limit=100):
        return self._db.query("SELECT * FROM batches ORDER BY created_at DESC LIMIT :n",
                              {"n": limit})

    def list_active(self):
        rows = self._db.query(
            "SELECT * FROM batches WHERE status = :a OR status = :b ORDER BY created_at",
            {"a": _ACTIVE[0], "b": _ACTIVE[1]})
        for r in rows:
            r["options"] = load_json(r["options"])
        return rows

    def list_items(self, batch_id):
        rows = self._db.query(
            "SELECT * FROM batch_items WHERE batch_id = :b ORDER BY seq", {"b": batch_id})
        for r in rows:
            r["payload"] = load_json(r["payload"])
        return rows

    def _touch_item(self, batch_id, seq, **fields):
        fields["updated_at"] = utc_now_iso()
        sets = ", ".join(f"{k} = :{k}" for k in fields)
        params = {**fields, "b": batch_id, "s": seq}
        self._db.execute(f"UPDATE batch_items SET {sets} WHERE batch_id = :b AND seq = :s", params)

    def set_item_materialized(self, batch_id, seq, request_id):
        self._touch_item(batch_id, seq, status="Materialized", request_id=request_id)

    def set_item_status(self, batch_id, seq, status, *, reason_code=None):
        self._touch_item(batch_id, seq, status=status, reason_code=reason_code)

    def reset_item_to_queued(self, batch_id, seq):
        self._touch_item(batch_id, seq, status="Queued", request_id=None, reason_code=None)

    def set_status(self, batch_id, status):
        self._db.execute(
            "UPDATE batches SET status = :s, updated_at = :now WHERE batch_id = :b",
            {"s": status, "now": utc_now_iso(), "b": batch_id})

    def bump_counts(self, batch_id, *, succeeded=0, failed=0):
        self._db.execute(
            """UPDATE batches SET succeeded_count = succeeded_count + :s,
                   failed_count = failed_count + :f, updated_at = :now WHERE batch_id = :b""",
            {"s": succeeded, "f": failed, "now": utc_now_iso(), "b": batch_id})

    def reset_failed_items(self, batch_id) -> int:
        rows = self._db.query(
            "SELECT seq FROM batch_items WHERE batch_id = :b AND (status = 'Failed' OR status = 'Rejected')",
            {"b": batch_id})
        for r in rows:
            self.reset_item_to_queued(batch_id, r["seq"])
        if rows:
            self.bump_counts(batch_id, failed=-len(rows))
        return len(rows)
```

`repositories/__init__.py`: `from .batches import BatchesRepository` + `self.batches = BatchesRepository(db)`.
`repositories/requests.py` `create`: `batch_id=None` 키워드 추가, INSERT에 `batch_id` 컬럼/`:bid` 값, params에 `"bid": batch_id`.

- [ ] **Step 4: 통과 + 회귀** — Run: `.venv/bin/pytest tests/test_repo_batches.py tests/test_api_requests.py -v && .venv/bin/pytest -q`.

- [ ] **Step 5: 커밋**
```bash
git add src/dms/repositories/batches.py src/dms/repositories/__init__.py src/dms/repositories/requests.py tests/test_repo_batches.py
git commit -m "feat(repo): BatchesRepository + requests.create batch_id"
```

---

## Task 4: BatchOrchestrator — scan 경로 (materialize 쓰로틀·집계·Completed)

**Files:**
- Create: `src/dms/batch_orchestrator.py`
- Test: `tests/test_batch_orchestrator_scan.py`

**Interfaces:**
- Consumes: `repos`(batches/requests/data_jobs), `build_data_payload`, `RequestState`/`DataJobState`.
- Produces: `BatchOrchestrator(repos, *, settings)`; `run_once()`. 내부 `_child_state(request_id) -> tuple`,
  `_materialize(batch, item)`, `_record_terminal(batch_id, item, req_state)`, `_drive(batch)`.
  스캔 경로: `Running` 배치에서 `max_concurrency - in_flight`만큼 Queued materialize; 자식 request가
  종단이면 item 종단화 + counts; 전 item 종단 시 배치 `Completed`.

- [ ] **Step 1: 실패 테스트 — `tests/test_batch_orchestrator_scan.py`**

```python
from dms.repositories import Repositories
from dms.batch_orchestrator import BatchOrchestrator
from dms.domain import RequestState

class _S:  # 최소 settings 더미
    preview_ttl_seconds = 900

def _orch(db):
    return BatchOrchestrator(Repositories(db), settings=_S())

def test_scan_throttles_materialize(db):
    repos = Repositories(db)
    bid = repos.batches.create(operation="scan", requester_id="admin", actor="admin",
        max_concurrency=2, options={}, note=None,
        items=[{"storage":"cephfs-dms","target":f"a{i}"} for i in range(5)], status="Running")
    _orch(db).run_once()
    mats = [it for it in repos.batches.list_items(bid) if it["status"]=="Materialized"]
    assert len(mats)==2                      # 상한 2만 materialize
    # 각 materialize된 자식은 실제 Pending 요청
    for it in mats:
        assert repos.requests.get(it["request_id"])["state"]=="Pending"
        assert repos.requests.get(it["request_id"])["batch_id"]==bid

def test_scan_aggregates_and_completes(db):
    repos = Repositories(db)
    bid = repos.batches.create(operation="scan", requester_id="admin", actor="admin",
        max_concurrency=5, options={}, note=None,
        items=[{"storage":"cephfs-dms","target":"a"},{"storage":"cephfs-dms","target":"b"}],
        status="Running")
    _orch(db).run_once()                     # 둘 다 materialize
    items = repos.batches.list_items(bid)
    # 자식 완료를 시뮬레이션: 하나 Succeeded, 하나 Failed
    repos.requests.set_state(items[0]["request_id"], RequestState.SUCCEEDED, actor="t")
    repos.requests.set_state(items[1]["request_id"], RequestState.FAILED, actor="t")
    _orch(db).run_once()                     # 집계 + Completed
    b = repos.batches.get(bid)
    assert b["status"]=="Completed" and b["succeeded_count"]==1 and b["failed_count"]==1
    sts = sorted(it["status"] for it in repos.batches.list_items(bid))
    assert sts==["Failed","Succeeded"]
```

- [ ] **Step 2: 실패 확인** — Run: `.venv/bin/pytest tests/test_batch_orchestrator_scan.py -v` → FAIL.

- [ ] **Step 3: 구현 — `src/dms/batch_orchestrator.py`** (scan 경로)

```python
from .domain import (Operation, RequestState, TERMINAL_REQUEST_STATES,
                     DataJobState, build_data_payload)

_ITEM_TERMINAL = {"Succeeded", "Failed", "Rejected", "Cancelled"}
_REQ_TERMINAL = {s.value for s in TERMINAL_REQUEST_STATES}

class BatchOrchestrator:
    def __init__(self, repos, *, settings):
        self._repos = repos
        self._settings = settings

    def run_once(self):
        for batch in self._repos.batches.list_active():
            self._drive(batch)

    def _child_state(self, request_id):
        req = self._repos.requests.get(request_id)
        if req is None:
            return ("in_flight", None)
        if req["state"] in _REQ_TERMINAL:
            return ("terminal", req["state"])
        jobs = self._repos.data_jobs.list_jobs(request_id=request_id)
        job = jobs[0] if jobs else None
        if job is not None and job["state"] == DataJobState.CONFIRM_PENDING.value:
            return ("previewed", job)
        return ("in_flight", None)

    def _record_terminal(self, batch_id, item, req_state):
        if req_state == RequestState.SUCCEEDED.value:
            self._repos.batches.set_item_status(batch_id, item["seq"], "Succeeded")
            self._repos.batches.bump_counts(batch_id, succeeded=1)
        else:
            status = "Rejected" if req_state == RequestState.REJECTED.value else "Failed"
            self._repos.batches.set_item_status(batch_id, item["seq"], status,
                                                reason_code=req_state)
            self._repos.batches.bump_counts(batch_id, failed=1)

    def _materialize(self, batch, item):
        payload, key = build_data_payload(batch["operation"], options=batch["options"],
                                          **item["payload"])
        rid = self._repos.requests.create(
            operation=batch["operation"], requester_id=batch["requester_id"],
            actor=batch["actor"], resource_key=key, payload=payload,
            priority="mid", batch_id=batch["batch_id"])
        self._repos.batches.set_item_materialized(batch["batch_id"], item["seq"], rid)

    def _drive(self, batch):
        bid = batch["batch_id"]
        items = self._repos.batches.list_items(bid)
        queued, in_flight, previewed, terminal = [], [], [], 0
        for item in items:
            st = item["status"]
            if st in _ITEM_TERMINAL:
                terminal += 1; continue
            if st == "Queued":
                queued.append(item); continue
            kind, info = self._child_state(item["request_id"])
            if kind == "terminal":
                self._record_terminal(bid, item, info); terminal += 1
            elif kind == "previewed":
                previewed.append((item, info))
            else:
                in_flight.append(item)
        total = len(items)
        if terminal == total:
            self._repos.batches.set_status(bid, "Completed")
            return
        if batch["status"] == "Running":
            slots = batch["max_concurrency"] - len(in_flight)
            for item in queued[:max(0, slots)]:
                self._materialize(batch, item)
```

(sync 분기 `Previewing`/confirm/preview-expiry는 Task 5에서 `_drive`를 확장한다.)

- [ ] **Step 4: 통과 + 회귀** — Run: `.venv/bin/pytest tests/test_batch_orchestrator_scan.py -v && .venv/bin/pytest -q`.

- [ ] **Step 5: 커밋**
```bash
git add src/dms/batch_orchestrator.py tests/test_batch_orchestrator_scan.py
git commit -m "feat(batch): orchestrator scan path (throttle materialize, aggregate, complete)"
```

---

## Task 5: BatchOrchestrator — sync 게이트 (Previewing→PreviewReady, confirm 쓰로틀, preview 만료)

**Files:**
- Modify: `src/dms/batch_orchestrator.py`
- Test: `tests/test_batch_orchestrator_sync.py`

**Interfaces:**
- Consumes: Task 4 orchestrator, `data_jobs.set_confirmed`/`set_job_state`, `DataJobState.EXECUTING`, `utc_now_iso`.
- Produces: `_drive`가 sync를 처리 — `Previewing`에서 Queued를 쓰로틀 materialize(preview 진행), 전
  item이 preview(ConfirmPending)/종단이면 배치 `PreviewReady`. `Running`(sync)에서 남은 Queued를
  materialize + ConfirmPending 자식을 쓰로틀 confirm(`set_confirmed`+`set_job_state(EXECUTING)`).
  `_confirm_child(item, job, now)`, preview 만료 자식 `reset_item_to_queued`.

- [ ] **Step 1: 실패 테스트 — `tests/test_batch_orchestrator_sync.py`**

```python
from dms.repositories import Repositories
from dms.batch_orchestrator import BatchOrchestrator
from dms.domain import DataJobState

class _S:
    preview_ttl_seconds = 900

def _orch(db): return BatchOrchestrator(Repositories(db), settings=_S())

def _make_confirmpending(repos, request_id, fp="fp-x"):
    # 자식이 preview 완료(ConfirmPending)한 상태를 시뮬레이션
    plan = repos.data_jobs.create_plan(request_id, actor="planner")
    jid = repos.data_jobs.create_job(request_id, plan, operation="sync", priority="mid",
        source_storage="s1", destination_storage="s2", source="a", destination="b",
        options={}, tool="dsync", worker_pool={}, precondition={}, actor="planner")
    repos.data_jobs.set_preview(jid, fingerprint=fp, expires_at="2099-01-01T00:00:00Z",
                                artifact_uri="x")
    repos.data_jobs.set_job_state(jid, DataJobState.CONFIRM_PENDING, actor="stepper")
    return jid

def test_sync_previewing_to_previewready(db):
    repos = Repositories(db)
    bid = repos.batches.create(operation="sync", requester_id="admin", actor="admin",
        max_concurrency=2, options={},
        items=[{"source_storage":"s1","source":"a","destination_storage":"s2","destination":"b"}],
        note=None, status="Previewing")
    _orch(db).run_once()                                  # materialize (1개)
    it = repos.batches.list_items(bid)[0]
    _make_confirmpending(repos, it["request_id"])         # preview 완료 시뮬
    _orch(db).run_once()                                  # 전원 previewed → PreviewReady
    assert repos.batches.get(bid)["status"]=="PreviewReady"

def test_sync_running_confirms_children(db):
    repos = Repositories(db)
    bid = repos.batches.create(operation="sync", requester_id="admin", actor="admin",
        max_concurrency=2, options={},
        items=[{"source_storage":"s1","source":"a","destination_storage":"s2","destination":"b"}],
        note=None, status="Previewing")
    _orch(db).run_once()
    it = repos.batches.list_items(bid)[0]
    jid = _make_confirmpending(repos, it["request_id"], fp="fp-9")
    _orch(db).run_once()                                  # PreviewReady
    repos.batches.set_status(bid, "Running")              # 운영자 배치 confirm 시뮬
    _orch(db).run_once()                                  # 자식 confirm
    job = repos.data_jobs.get_job(jid)
    assert job["state"]=="Executing" and job["confirmed_fingerprint"]=="fp-9"
```

- [ ] **Step 2: 실패 확인** — Run: `.venv/bin/pytest tests/test_batch_orchestrator_sync.py -v` → FAIL.

- [ ] **Step 3: 구현 — `_drive` 확장 + `_confirm_child`**

`batch_orchestrator.py` 상단 import에 `from .db import utc_now_iso` 추가. `_drive`에서 `terminal==total`
Completed 체크 뒤, `Running` 분기 앞에 sync 로직을 넣어 아래 최종형으로 만든다:

```python
        total = len(items)
        if terminal == total:
            self._repos.batches.set_status(bid, "Completed"); return
        now = utc_now_iso()
        if batch["status"] == "Previewing":
            if not queued and not in_flight:          # 전원 previewed(또는 종단)
                self._repos.batches.set_status(bid, "PreviewReady"); return
            slots = batch["max_concurrency"] - len(in_flight)
            for item in queued[:max(0, slots)]:
                self._materialize(batch, item)
            for item, job in previewed:               # preview 만료 재시도
                if job.get("preview_expires_at") and job["preview_expires_at"] < now:
                    self._repos.batches.reset_item_to_queued(bid, item["seq"])
            return
        if batch["status"] == "Running":
            slots = batch["max_concurrency"] - len(in_flight)
            for item in queued[:max(0, slots)]:
                self._materialize(batch, item); slots -= 1
            if batch["operation"] == "sync":
                for item, job in previewed[:max(0, slots)]:
                    self._confirm_child(item, job, now)
```

```python
    def _confirm_child(self, item, job, now):
        if job.get("preview_expires_at") and job["preview_expires_at"] < now:
            self._repos.batches.reset_item_to_queued(item["batch_id"], item["seq"]); return
        self._repos.data_jobs.set_confirmed(job["job_id"], job["preview_fingerprint"])
        self._repos.data_jobs.set_job_state(job["job_id"], DataJobState.EXECUTING, actor="batch-orchestrator")
```
(`item["batch_id"]`는 list_items 행에 포함됨.) Task 4의 scan 전용 `Running` 분기는 위 통합 분기로
대체된다(scan은 operation!=sync라 confirm 루프를 건너뜀).

- [ ] **Step 4: 통과 + 회귀** — Run: `.venv/bin/pytest tests/test_batch_orchestrator_sync.py tests/test_batch_orchestrator_scan.py -v && .venv/bin/pytest -q`.

- [ ] **Step 5: 커밋**
```bash
git add src/dms/batch_orchestrator.py tests/test_batch_orchestrator_sync.py
git commit -m "feat(batch): orchestrator sync gate (preview-ready, throttled confirm, expiry)"
```

---

## Task 6: 컨트롤러 루프 + config interval

**Files:**
- Modify: `src/dms/config.py`, `src/dms/controller.py`
- Test: `tests/test_config_batch.py`, `tests/test_controller_batch.py`

**Interfaces:**
- Consumes: `BatchOrchestrator`(Task 4-5), `Settings`.
- Produces: `settings.batch_orchestrator_interval_seconds`(기본 5); `build_loops`가
  `Loop("batch-orchestrator", settings.batch_orchestrator_interval_seconds, ...)` 포함.

- [ ] **Step 1: 실패 테스트**

```python
# tests/test_config_batch.py
from dms.config import Settings
def test_batch_interval_default():
    s = Settings.from_env({"DMS_DATABASE_URL":"x","DMS_SHARED_TOKEN":"a"*8,
        "DMS_ADMIN_TOKEN":"b"*8,"DMS_SESSION_SECRET":"c"*8})
    assert s.batch_orchestrator_interval_seconds == 5
def test_batch_interval_override():
    s = Settings.from_env({"DMS_DATABASE_URL":"x","DMS_SHARED_TOKEN":"a"*8,
        "DMS_ADMIN_TOKEN":"b"*8,"DMS_SESSION_SECRET":"c"*8,
        "DMS_BATCH_ORCHESTRATOR_INTERVAL_SECONDS":"9"})
    assert s.batch_orchestrator_interval_seconds == 9
```
```python
# tests/test_controller_batch.py
from dms.controller import build_loops
from dms.repositories import Repositories
def test_batch_orchestrator_loop_registered(db, settings):
    names = [l.name for l in build_loops(settings, Repositories(db))]
    assert "batch-orchestrator" in names
```
(`tests/test_config_batch.py`의 env dict는 기존 `tests/test_config_phase3c.py`의 `VALID`와 동일한
필수 키를 맞춘다 — 그 파일을 열어 정확한 키 세트를 복사할 것.)

- [ ] **Step 2: 실패 확인** — Run: `.venv/bin/pytest tests/test_config_batch.py tests/test_controller_batch.py -v` → FAIL.

- [ ] **Step 3: 구현**

`config.py`: `_SERVER_INT_KEYS`에 `("DMS_BATCH_ORCHESTRATOR_INTERVAL_SECONDS", "batch_orchestrator_interval_seconds", 5)` 추가; `Settings`에 `batch_orchestrator_interval_seconds: int = 5` 필드 추가.

`controller.py`: `from .batch_orchestrator import BatchOrchestrator` 추가; `build_loops` 반환 리스트에
```python
        Loop("batch-orchestrator", settings.batch_orchestrator_interval_seconds,
             lambda: BatchOrchestrator(repos, settings=settings).run_once()),
```
추가. **주의**: `tests/test_controller_stepper.py`의 더미 `_Settings` 클래스에도
`batch_orchestrator_interval_seconds = 5`를 추가(안 그러면 그 테스트가 build_loops에서 AttributeError).
또한 `tests/test_controller.py`의 `test_build_loops_names_and_intervals` 기대 리스트에 batch-orchestrator
항목을 추가.

- [ ] **Step 4: 통과 + 회귀** — Run: `.venv/bin/pytest tests/test_config_batch.py tests/test_controller_batch.py tests/test_controller.py tests/test_controller_stepper.py -v && .venv/bin/pytest -q`.

- [ ] **Step 5: 커밋**
```bash
git add src/dms/config.py src/dms/controller.py tests/test_config_batch.py tests/test_controller_batch.py tests/test_controller.py tests/test_controller_stepper.py
git commit -m "feat(controller): register batch-orchestrator loop + interval config"
```

---

## Task 7: API — routes_batches

**Files:**
- Create: `src/dms/api/routes_batches.py`
- Modify: `src/dms/api/app.py`
- Test: `tests/test_api_batches.py`

**Interfaces:**
- Consumes: `require_admin`(auth.py), `repos.batches`, `validate_batch`/`build_data_payload`(도메인),
  `DomainValidationError`.
- Produces 라우터:
  - `POST /api/admin/batches` `{operation, max_concurrency, options, note, items:[...]}` → `{batch_id, status}`(202). 각 item을 `build_data_payload`로 검증(실패 시 422 + reason). status = scan→Running, sync→Previewing.
  - `GET /api/admin/batches` → 목록. `GET /api/admin/batches/{id}` → `{...batch, items:[...]}`.
  - `POST /api/admin/batches/{id}:confirm` (PreviewReady→Running; 아니면 409 `batch_not_confirmable`).
  - `POST /api/admin/batches/{id}:rerun-failed` (failed 리셋 + Running; 실패 없으면 409 `no_failed_items`).
  - `POST /api/admin/batches/{id}:cancel` (Queued/Materialized item Cancelled + 배치 Cancelled).

- [ ] **Step 1: 실패 테스트 — `tests/test_api_batches.py`**

```python
def _admin(client):  # 세션 로그인(admin) — 기존 test_api_auth 패턴 재사용
    client.app.state.repos.accounts.create("admin","pw","admin",actor="t")
    client.post("/api/auth/login", json={"username":"admin","password":"pw"})

def test_create_and_get_batch(client):
    _admin(client)
    r = client.post("/api/admin/batches", json={"operation":"scan","max_concurrency":2,
        "options":{},"note":"n","items":[{"storage":"s1","target":"a"},{"storage":"s1","target":"b"}]})
    assert r.status_code==202
    bid = r.json()["batch_id"]
    assert r.json()["status"]=="Running"
    d = client.get(f"/api/admin/batches/{bid}").json()
    assert d["item_count"]==2 and len(d["items"])==2

def test_create_sync_is_previewing(client):
    _admin(client)
    r = client.post("/api/admin/batches", json={"operation":"sync","max_concurrency":1,
        "options":{},"note":None,"items":[{"source_storage":"s1","source":"a",
        "destination_storage":"s2","destination":"b"}]})
    assert r.json()["status"]=="Previewing"

def test_create_rejects_bad_item(client):
    _admin(client)
    r = client.post("/api/admin/batches", json={"operation":"scan","max_concurrency":1,
        "options":{},"note":None,"items":[{"storage":"s1","target":"../bad"}]})
    assert r.status_code==422

def test_create_rejects_empty(client):
    _admin(client)
    r = client.post("/api/admin/batches", json={"operation":"scan","max_concurrency":1,
        "options":{},"note":None,"items":[]})
    assert r.status_code==422 and r.json()["detail"]=="empty_batch"

def test_confirm_requires_previewready(client):
    _admin(client)
    bid = client.post("/api/admin/batches", json={"operation":"scan","max_concurrency":1,
        "options":{},"note":None,"items":[{"storage":"s1","target":"a"}]}).json()["batch_id"]
    r = client.post(f"/api/admin/batches/{bid}:confirm")     # scan은 Running이라 confirm 불가
    assert r.status_code==409

def test_requires_admin(client):
    r = client.get("/api/admin/batches")
    assert r.status_code==401
```

- [ ] **Step 2: 실패 확인** — Run: `.venv/bin/pytest tests/test_api_batches.py -v` → FAIL.

- [ ] **Step 3: 구현 — `src/dms/api/routes_batches.py`**

```python
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from ..domain import DomainValidationError, Operation, build_data_payload, validate_batch
from .auth import Identity, require_admin

router = APIRouter()

class BatchBody(BaseModel):
    operation: str
    max_concurrency: int
    options: dict = {}
    note: str | None = None
    items: list[dict]

@router.post("/api/admin/batches", status_code=202)
def create_batch(body: BatchBody, request: Request, identity: Identity = Depends(require_admin)):
    try:
        validate_batch(body.operation, body.max_concurrency, body.items)
        for item in body.items:                       # 각 행 검증(조기 거부)
            build_data_payload(body.operation, options=body.options, **item)
    except (DomainValidationError, TypeError) as e:
        raise HTTPException(status_code=422, detail=getattr(e, "reason_code", "invalid_batch"))
    status = "Running" if body.operation == Operation.SCAN.value else "Previewing"
    bid = request.app.state.repos.batches.create(
        operation=body.operation, requester_id=identity.actor, actor=identity.actor,
        max_concurrency=body.max_concurrency, options=body.options, note=body.note,
        items=body.items, status=status)
    return {"batch_id": bid, "status": status}

@router.get("/api/admin/batches")
def list_batches(request: Request, identity: Identity = Depends(require_admin)):
    return request.app.state.repos.batches.list()

@router.get("/api/admin/batches/{batch_id}")
def get_batch(batch_id: str, request: Request, identity: Identity = Depends(require_admin)):
    repo = request.app.state.repos.batches
    b = repo.get(batch_id)
    if b is None:
        raise HTTPException(status_code=404, detail="batch_not_found")
    b["items"] = repo.list_items(batch_id)
    return b

@router.post("/api/admin/batches/{batch_id}:confirm")
def confirm_batch(batch_id: str, request: Request, identity: Identity = Depends(require_admin)):
    repo = request.app.state.repos.batches
    b = repo.get(batch_id)
    if b is None:
        raise HTTPException(status_code=404, detail="batch_not_found")
    if b["status"] != "PreviewReady":
        raise HTTPException(status_code=409, detail="batch_not_confirmable")
    repo.set_status(batch_id, "Running")
    return {"status": "Running"}

@router.post("/api/admin/batches/{batch_id}:rerun-failed")
def rerun_failed(batch_id: str, request: Request, identity: Identity = Depends(require_admin)):
    repo = request.app.state.repos.batches
    b = repo.get(batch_id)
    if b is None:
        raise HTTPException(status_code=404, detail="batch_not_found")
    n = repo.reset_failed_items(batch_id)
    if n == 0:
        raise HTTPException(status_code=409, detail="no_failed_items")
    repo.set_status(batch_id, "Running")
    return {"status": "Running", "requeued": n}

@router.post("/api/admin/batches/{batch_id}:cancel")
def cancel_batch(batch_id: str, request: Request, identity: Identity = Depends(require_admin)):
    repo = request.app.state.repos.batches
    b = repo.get(batch_id)
    if b is None:
        raise HTTPException(status_code=404, detail="batch_not_found")
    for it in repo.list_items(batch_id):
        if it["status"] in ("Queued", "Materialized"):
            repo.set_item_status(batch_id, it["seq"], "Cancelled")
    repo.set_status(batch_id, "Cancelled")
    return {"status": "Cancelled"}
```
`app.py`: `from .routes_batches import router as batches_router` + `app.include_router(batches_router)`
(다른 include_router 옆, SPA 캐치올 이전).

> 주: `build_data_payload(operation, options=, **item)`에서 item에 예상 밖 키가 있으면 `TypeError`
> → 422로 매핑(위 except에 포함). 정상 키만 허용해 조기 거부.

- [ ] **Step 4: 통과 + 회귀** — Run: `.venv/bin/pytest tests/test_api_batches.py -v && .venv/bin/pytest -q`.

- [ ] **Step 5: 커밋**
```bash
git add src/dms/api/routes_batches.py src/dms/api/app.py tests/test_api_batches.py
git commit -m "feat(api): /api/admin/batches CRUD + confirm/rerun/cancel"
```

---

## Task 8: 프론트 — 타입 + CSV 파서 + 훅

**Files:**
- Modify: `frontend/src/lib/types.ts`
- Create: `frontend/src/lib/csv.ts`, `frontend/src/features/batches/useBatches.ts`
- Test: `frontend/src/lib/csv.test.ts`, `frontend/src/features/batches/useBatches.test.tsx`

**Interfaces:**
- Consumes: `apiGet`/`apiSend`(lib/api), TanStack Query.
- Produces:
  - `types.ts`: `Batch`(batch_id, operation, status, max_concurrency, item_count, succeeded_count, failed_count, note, created_at), `BatchItem`(seq, payload, status, request_id, reason_code), `BatchDetail = Batch & { items: BatchItem[] }`.
  - `csv.ts`: `parseBatchCsv(operation: "scan"|"sync", text: string): { rows: Record<string,string>[]; errors: string[] }`. scan 헤더 `storage,target`; sync 헤더 `source_storage,source,destination_storage,destination`. 헤더 검증 + 빈/부족 행 에러.
  - `useBatches.ts`: `useBatches()`(폴링), `useBatch(id)`(비종단 폴링), `useCreateBatch()`, `useConfirmBatch(id)`, `useRerunFailed(id)`, `useCancelBatch(id)`.

- [ ] **Step 1: 실패 테스트 — `csv.test.ts`**

```ts
import { parseBatchCsv } from "./csv";
import { test, expect } from "vitest";

test("scan csv ok", () => {
  const { rows, errors } = parseBatchCsv("scan", "storage,target\ncephfs-dms,a/b\ncephfs-dms,c");
  expect(errors).toEqual([]);
  expect(rows).toEqual([{storage:"cephfs-dms",target:"a/b"},{storage:"cephfs-dms",target:"c"}]);
});
test("sync csv ok", () => {
  const { rows } = parseBatchCsv("sync",
    "source_storage,source,destination_storage,destination\ns1,a,s2,b");
  expect(rows).toEqual([{source_storage:"s1",source:"a",destination_storage:"s2",destination:"b"}]);
});
test("wrong header is an error", () => {
  const { errors } = parseBatchCsv("scan", "foo,bar\n1,2");
  expect(errors.length).toBeGreaterThan(0);
});
test("short row is an error, not silently dropped", () => {
  const { errors } = parseBatchCsv("scan", "storage,target\ncephfs-dms");
  expect(errors.length).toBeGreaterThan(0);
});
```

- [ ] **Step 2: 실패 확인** — Run: `cd frontend && npx vitest run src/lib/csv.test.ts` → FAIL.

- [ ] **Step 3: 구현 — `csv.ts` + `types.ts` + `useBatches.ts`**

```ts
// csv.ts
const HEADERS = {
  scan: ["storage", "target"],
  sync: ["source_storage", "source", "destination_storage", "destination"],
} as const;

export function parseBatchCsv(operation: "scan" | "sync", text: string) {
  const errors: string[] = [];
  const lines = text.split(/\r?\n/).map((l) => l.trim()).filter((l) => l.length > 0);
  if (lines.length === 0) return { rows: [], errors: ["빈 CSV"] };
  const want = HEADERS[operation];
  const header = lines[0].split(",").map((c) => c.trim());
  if (header.length !== want.length || want.some((w, i) => header[i] !== w)) {
    return { rows: [], errors: [`헤더가 ${want.join(",")} 이어야 합니다`] };
  }
  const rows: Record<string, string>[] = [];
  lines.slice(1).forEach((line, i) => {
    const cells = line.split(",").map((c) => c.trim());
    if (cells.length !== want.length || cells.some((c) => c === "")) {
      errors.push(`${i + 2}행: 열 수/빈 값 오류`);
      return;
    }
    rows.push(Object.fromEntries(want.map((w, j) => [w, cells[j]])));
  });
  return { rows, errors };
}
```

```ts
// types.ts 에 추가
export interface Batch {
  batch_id: string; operation: string; status: string; max_concurrency: number;
  item_count: number; succeeded_count: number; failed_count: number;
  note: string | null; created_at: string;
}
export interface BatchItem {
  seq: number; payload: Record<string, unknown>; status: string;
  request_id: string | null; reason_code: string | null;
}
export interface BatchDetail extends Batch { items: BatchItem[] }
```

```ts
// useBatches.ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGet, apiSend } from "../../lib/api";
import type { Batch, BatchDetail } from "../../lib/types";

const BATCH_TERMINAL = new Set(["Completed", "Cancelled"]);
export const useBatches = () =>
  useQuery({ queryKey: ["batches"], queryFn: () => apiGet<Batch[]>("/api/admin/batches"),
            refetchInterval: 4000 });
export const useBatch = (id: string) =>
  useQuery({ queryKey: ["batch", id], queryFn: () => apiGet<BatchDetail>(`/api/admin/batches/${id}`),
    refetchInterval: (q) => (BATCH_TERMINAL.has((q.state.data as BatchDetail | undefined)?.status ?? "") ? false : 2500) });
export interface CreateBatchBody {
  operation: string; max_concurrency: number; options: Record<string, unknown>;
  note: string | null; items: Record<string, string>[];
}
export const useCreateBatch = () =>
  useMutation({ mutationFn: (b: CreateBatchBody) =>
    apiSend<{ batch_id: string; status: string }>("POST", "/api/admin/batches", b) });
function _action(id: string, verb: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiSend("POST", `/api/admin/batches/${id}:${verb}`),
    onSettled: () => { qc.invalidateQueries({ queryKey: ["batch", id] });
                       qc.invalidateQueries({ queryKey: ["batches"] }); },
  });
}
export const useConfirmBatch = (id: string) => _action(id, "confirm");
export const useRerunFailed = (id: string) => _action(id, "rerun-failed");
export const useCancelBatch = (id: string) => _action(id, "cancel");
```

- [ ] **Step 4: `useBatches.test.tsx`(훅 최소 검증) + 통과**

```ts
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { useBatches } from "./useBatches";
const server = setupServer();
beforeAll(() => server.listen()); afterEach(() => server.resetHandlers()); afterAll(() => server.close());
test("useBatches fetches list", async () => {
  server.use(http.get("/api/admin/batches", () => HttpResponse.json([{batch_id:"b1",operation:"scan",
    status:"Running",max_concurrency:2,item_count:3,succeeded_count:1,failed_count:0,note:null,created_at:""}])));
  const qc = new QueryClient({ defaultOptions:{ queries:{ retry:false }}});
  const { result } = renderHook(() => useBatches(), { wrapper: ({children}) =>
    <QueryClientProvider client={qc}>{children}</QueryClientProvider> });
  await waitFor(() => expect(result.current.data?.[0].batch_id).toBe("b1"));
});
```
Run: `cd frontend && npm test && npx tsc -b` → PASS.

- [ ] **Step 5: 커밋**
```bash
git add frontend/src/lib/csv.ts frontend/src/lib/csv.test.ts frontend/src/lib/types.ts frontend/src/features/batches/useBatches.ts frontend/src/features/batches/useBatches.test.tsx
git commit -m "feat(portal): batch types, CSV parser, and query hooks"
```

---

## Task 9: 프론트 — 배치 목록 + 생성 화면

**Files:**
- Create: `frontend/src/features/batches/BatchesList.tsx`, `frontend/src/features/batches/BatchCreate.tsx`
- Test: `frontend/src/features/batches/BatchCreate.test.tsx`

**Interfaces:**
- Consumes: `useBatches`/`useCreateBatch`(Task 8), `parseBatchCsv`, Table/StatusPill/Card/Button/Field, useNavigate.
- Produces: `<BatchesList/>`(목록+생성 링크), `<BatchCreate/>`(operation·CSV·options·max_concurrency·note → 제출→상세 이동).

- [ ] **Step 1: 실패 테스트 — `BatchCreate.test.tsx`**

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { BatchCreate } from "./BatchCreate";
const server = setupServer();
beforeAll(() => server.listen()); afterEach(() => server.resetHandlers()); afterAll(() => server.close());

test("parses CSV, submits items, navigates to detail", async () => {
  let body: any = null;
  server.use(http.post("/api/admin/batches", async ({request}) => {
    body = await request.json();
    return HttpResponse.json({batch_id:"b9", status:"Running"}, {status:202}); }));
  const qc = new QueryClient({ defaultOptions:{ queries:{ retry:false }}});
  render(<QueryClientProvider client={qc}><MemoryRouter initialEntries={["/admin/batches/new"]}>
    <Routes><Route path="/admin/batches/new" element={<BatchCreate/>} />
      <Route path="/admin/batches/:id" element={<h1>배치 b9</h1>} /></Routes>
  </MemoryRouter></QueryClientProvider>);
  await userEvent.type(screen.getByLabelText("CSV"), "storage,target\ncephfs-dms,a\ncephfs-dms,b");
  await userEvent.clear(screen.getByLabelText("동시 실행 상한"));
  await userEvent.type(screen.getByLabelText("동시 실행 상한"), "2");
  await userEvent.click(screen.getByRole("button", { name: "배치 생성" }));
  expect(await screen.findByRole("heading", { name: "배치 b9" })).toBeInTheDocument();
  expect(body).toMatchObject({ operation:"scan", max_concurrency:2,
    items:[{storage:"cephfs-dms",target:"a"},{storage:"cephfs-dms",target:"b"}] });
});
```

- [ ] **Step 2: 실패 확인** — Run: `cd frontend && npx vitest run src/features/batches/BatchCreate.test.tsx` → FAIL.

- [ ] **Step 3: 구현 — `BatchesList.tsx` + `BatchCreate.tsx`**

```tsx
// BatchesList.tsx
import { Link } from "react-router-dom";
import { useBatches } from "./useBatches";
import { Table } from "../../components/ui/Table";
import { StatusPill } from "../../components/ui/StatusPill";
import { Button } from "../../components/ui/Button";
export function BatchesList() {
  const q = useBatches();
  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">배치 작업</h1>
        <Link to="/admin/batches/new"><Button>배치 생성</Button></Link>
      </div>
      {q.isLoading ? <p className="text-muted">불러오는 중…</p> : (
        <Table>
          <thead><tr className="text-muted"><th className="py-2">배치</th><th>작업</th><th>상태</th><th>진행</th></tr></thead>
          <tbody>
            {(q.data ?? []).map((b) => (
              <tr key={b.batch_id} className="border-t border-black/5">
                <td className="py-2"><Link className="text-accent" to={`/admin/batches/${b.batch_id}`}>{b.batch_id.slice(0,12)}</Link></td>
                <td>{b.operation}</td><td><StatusPill state={b.status} /></td>
                <td className="text-muted">{b.succeeded_count}/{b.failed_count}/{b.item_count}</td>
              </tr>))}
          </tbody>
        </Table>)}
    </section>
  );
}
```

```tsx
// BatchCreate.tsx
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useCreateBatch } from "./useBatches";
import { parseBatchCsv } from "../../lib/csv";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { ApiError } from "../../lib/api";
const field = "mt-1 w-full rounded-lg border border-black/10 px-3 py-2";
export function BatchCreate() {
  const nav = useNavigate(); const create = useCreateBatch();
  const [op, setOp] = useState<"scan"|"sync">("scan");
  const [csv, setCsv] = useState(""); const [mc, setMc] = useState(2);
  const [note, setNote] = useState("");
  const { rows, errors } = parseBatchCsv(op, csv);
  return (
    <Card className="max-w-2xl">
      <h1 className="text-lg font-semibold mb-4">배치 생성</h1>
      <form className="space-y-3" onSubmit={(e) => { e.preventDefault();
        if (errors.length || rows.length === 0) return;
        create.mutate({ operation: op, max_concurrency: mc, options: {}, note: note || null, items: rows },
          { onSuccess: (r) => nav(`/admin/batches/${r.batch_id}`) }); }}>
        <label className="text-sm block">작업
          <select aria-label="작업" className={field} value={op}
                  onChange={(e) => setOp(e.target.value as "scan"|"sync")}>
            <option value="scan">scan</option><option value="sync">sync</option>
          </select></label>
        <label className="text-sm block">CSV ({op === "scan" ? "storage,target" : "source_storage,source,destination_storage,destination"})
          <textarea aria-label="CSV" className={`${field} h-40 font-mono`} value={csv}
                    onChange={(e) => setCsv(e.target.value)} /></label>
        <label className="text-sm block">동시 실행 상한
          <input aria-label="동시 실행 상한" type="number" min={1} className={field} value={mc}
                 onChange={(e) => setMc(Number(e.target.value))} /></label>
        <label className="text-sm block">메모
          <input aria-label="메모" className={field} value={note} onChange={(e) => setNote(e.target.value)} /></label>
        <div className="text-sm text-muted">파싱된 행: {rows.length}
          {errors.length > 0 && <span className="text-bad"> · 오류 {errors.length}: {errors[0]}</span>}</div>
        {create.isError && <p className="text-bad text-sm">{(create.error as ApiError).message}</p>}
        <Button type="submit" disabled={create.isPending || errors.length>0 || rows.length===0}>배치 생성</Button>
      </form>
    </Card>
  );
}
```

- [ ] **Step 4: 통과 + 회귀** — Run: `cd frontend && npm test && npx tsc -b` → PASS.

- [ ] **Step 5: 커밋**
```bash
git add frontend/src/features/batches/BatchesList.tsx frontend/src/features/batches/BatchCreate.tsx frontend/src/features/batches/BatchCreate.test.tsx
git commit -m "feat(portal): batch list + create (CSV) screens"
```

---

## Task 10: 프론트 — 배치 상세 (items 테이블 + confirm/rerun/cancel)

**Files:**
- Create: `frontend/src/features/batches/BatchDetail.tsx`
- Test: `frontend/src/features/batches/BatchDetail.test.tsx`

**Interfaces:**
- Consumes: `useBatch`/`useConfirmBatch`/`useRerunFailed`/`useCancelBatch`(Task 8), Card/Table/StatusPill/Button, useParams.
- Produces: `<BatchDetail/>` — 배치 status·진행률 + items 테이블. `PreviewReady`면 "배치 확인" 버튼, `Completed`+failed>0이면 "실패분 재실행", `Running`/`Previewing`이면 "취소".

- [ ] **Step 1: 실패 테스트 — `BatchDetail.test.tsx`**

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { BatchDetail } from "./BatchDetail";
const server = setupServer();
beforeAll(() => server.listen()); afterEach(() => server.resetHandlers()); afterAll(() => server.close());
const batch = (over: any = {}) => ({ batch_id:"b1", operation:"sync", status:"PreviewReady",
  max_concurrency:2, item_count:1, succeeded_count:0, failed_count:0, note:null, created_at:"",
  items:[{seq:0, payload:{source:"a"}, status:"Materialized", request_id:"r1", reason_code:null}], ...over });

function renderAt(state: string) {
  server.use(http.get("/api/admin/batches/b1", () => HttpResponse.json(batch({status:state}))));
  const qc = new QueryClient({ defaultOptions:{ queries:{ retry:false }}});
  return render(<QueryClientProvider client={qc}><MemoryRouter initialEntries={["/admin/batches/b1"]}>
    <Routes><Route path="/admin/batches/:batchId" element={<BatchDetail/>} /></Routes>
  </MemoryRouter></QueryClientProvider>);
}

test("PreviewReady shows confirm button and posts confirm", async () => {
  let confirmed = false;
  server.use(http.post("/api/admin/batches/b1:confirm", () => { confirmed = true; return HttpResponse.json({status:"Running"}); }));
  renderAt("PreviewReady");
  await userEvent.click(await screen.findByRole("button", { name: "배치 확인" }));
  expect(confirmed).toBe(true);
});
test("renders items table with status", async () => {
  renderAt("Running");
  expect(await screen.findByText("Materialized")).toBeInTheDocument();
});
```

- [ ] **Step 2: 실패 확인** — Run: `cd frontend && npx vitest run src/features/batches/BatchDetail.test.tsx` → FAIL.

- [ ] **Step 3: 구현 — `BatchDetail.tsx`**

```tsx
import { useParams } from "react-router-dom";
import { useBatch, useConfirmBatch, useRerunFailed, useCancelBatch } from "./useBatches";
import { Card } from "../../components/ui/Card";
import { Table } from "../../components/ui/Table";
import { StatusPill } from "../../components/ui/StatusPill";
import { Button } from "../../components/ui/Button";
export function BatchDetail() {
  const { batchId = "" } = useParams();
  const q = useBatch(batchId);
  const confirm = useConfirmBatch(batchId);
  const rerun = useRerunFailed(batchId);
  const cancel = useCancelBatch(batchId);
  const b = q.data;
  return (
    <section className="space-y-4">
      <h1 className="text-lg font-semibold">배치 {batchId.slice(0,12)}</h1>
      <Card>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <StatusPill state={b?.status ?? "…"} />
            <span className="text-muted text-sm">{b?.operation} · 성공 {b?.succeeded_count}/실패 {b?.failed_count}/전체 {b?.item_count}</span>
          </div>
          <div className="flex gap-2">
            {b?.status === "PreviewReady" && <Button disabled={confirm.isPending} onClick={() => confirm.mutate()}>배치 확인</Button>}
            {b?.status === "Completed" && (b?.failed_count ?? 0) > 0 && <Button disabled={rerun.isPending} onClick={() => rerun.mutate()}>실패분 재실행</Button>}
            {(b?.status === "Running" || b?.status === "Previewing") && <Button variant="ghost" disabled={cancel.isPending} onClick={() => cancel.mutate()}>취소</Button>}
          </div>
        </div>
      </Card>
      <Table>
        <thead><tr className="text-muted"><th className="py-2">#</th><th>대상</th><th>상태</th><th>사유</th></tr></thead>
        <tbody>
          {(b?.items ?? []).map((it) => (
            <tr key={it.seq} className="border-t border-black/5">
              <td className="py-2">{it.seq}</td>
              <td className="text-muted font-mono text-xs">{JSON.stringify(it.payload)}</td>
              <td><StatusPill state={it.status} /></td>
              <td className="text-bad text-xs">{it.reason_code ?? ""}</td>
            </tr>))}
        </tbody>
      </Table>
    </section>
  );
}
```

- [ ] **Step 4: 통과 + 회귀** — Run: `cd frontend && npm test && npx tsc -b` → PASS.

- [ ] **Step 5: 커밋**
```bash
git add frontend/src/features/batches/BatchDetail.tsx frontend/src/features/batches/BatchDetail.test.tsx
git commit -m "feat(portal): batch detail (items table, confirm/rerun/cancel)"
```

---

## Task 11: 프론트 — 배치 내비 활성 + 라우트

**Files:**
- Modify: `frontend/src/app/AppShell.tsx`, `frontend/src/app/router.tsx`
- Test: `frontend/src/app/router.test.tsx` (기존에 배치 라우트 테스트 1건 추가)

**Interfaces:**
- Consumes: BatchesList/BatchCreate/BatchDetail(Task 9-10), RequireRole/AppShell.
- Produces: admin 내비 "배치 작업" 활성 링크(`/admin/batches`); 라우트 `/admin/batches`,
  `/admin/batches/new`, `/admin/batches/:batchId`(모두 `RequireRole role="admin"` + AppShell).

- [ ] **Step 1: 실패 테스트 — `router.test.tsx`에 추가**

```tsx
test("admin can open batches list", async () => {
  server.use(
    http.get("/api/auth/me", () => HttpResponse.json({ actor:"admin", role:"admin" })),
    http.get("/api/admin/batches", () => HttpResponse.json([])),
  );
  renderAt("/admin/batches");
  expect(await screen.findByRole("heading", { name: "배치 작업" })).toBeInTheDocument();
});
```
(기존 `router.test.tsx`의 `renderAt`/server 설정 재사용.)

- [ ] **Step 2: 실패 확인** — Run: `cd frontend && npx vitest run src/app/router.test.tsx` → FAIL(라우트 없음).

- [ ] **Step 3: 구현**

`AppShell.tsx`: 비활성 span(15-16줄)을 admin 링크로 교체:
```tsx
        {isAdmin && <NavLink to="/admin/batches" className={linkCls}>배치 작업</NavLink>}
```

`router.tsx`: import 추가
```tsx
import { BatchesList } from "../features/batches/BatchesList";
import { BatchCreate } from "../features/batches/BatchCreate";
import { BatchDetail } from "../features/batches/BatchDetail";
```
`<Routes>`에 3라우트 추가(다른 admin 라우트 옆):
```tsx
        <Route path="/admin/batches" element={<RequireRole role="admin"><AppShell><BatchesList /></AppShell></RequireRole>} />
        <Route path="/admin/batches/new" element={<RequireRole role="admin"><AppShell><BatchCreate /></AppShell></RequireRole>} />
        <Route path="/admin/batches/:batchId" element={<RequireRole role="admin"><AppShell><BatchDetail /></AppShell></RequireRole>} />
```

- [ ] **Step 4: 통과 + 전체 회귀** — Run: `cd frontend && npm test && npx tsc -b` → PASS(전체 프론트 스위트). 이어 백엔드 `.venv/bin/pytest -q`도 green 확인.

- [ ] **Step 5: 커밋**
```bash
git add frontend/src/app/AppShell.tsx frontend/src/app/router.tsx frontend/src/app/router.test.tsx
git commit -m "feat(portal): enable batch nav + routes"
```

---

## Self-Review (작성자 체크)

**1. Spec coverage**
- §1 라이프사이클(scan/sync, PreviewReady, 재실행, 취소) → Task 4/5/7. ✅
- §2 데이터 모델(batches/batch_items/requests.batch_id) → Task 2/3. ✅
- §3 오케스트레이터(쓰로틀 materialize, 집계, sync confirm, 만료) → Task 4/5. ✅
- §4 API(create/list/detail/confirm/rerun/cancel + reason_code) → Task 7. ✅
- §5 포탈(목록/생성 CSV/상세, 내비 활성, CSV 파싱) → Task 8/9/10/11. ✅
- §6 테스트(백엔드 repo/orchestrator/API + 프론트 CSV/화면) → 각 Task. ✅

**2. Placeholder scan:** 모든 코드 단계에 실제 코드. "적절히 처리" 없음. ✅

**3. Type consistency:** `build_data_payload`/`validate_batch`(T1)→오케스트레이터(T4/5)·API(T7) 동일 시그니처.
`BatchesRepository` 메서드명(create/get/list/list_active/list_items/set_item_materialized/set_item_status/
reset_item_to_queued/set_status/bump_counts/reset_failed_items)이 T3 정의와 T4/5/7 사용 일치. 배치 상태
문자열(Previewing/PreviewReady/Running/Completed/Cancelled)·item 상태(Queued/Materialized/Succeeded/
Failed/Rejected/Cancelled)가 백엔드·프론트 전반 일치. 프론트 훅명(useBatches/useBatch/useCreateBatch/
useConfirmBatch/useRerunFailed/useCancelBatch)·타입(Batch/BatchItem/BatchDetail) T8 정의 = T9/10/11 사용. ✅

## 배포/실증 (구현 후 별도 ops — 플랜 밖)
전 Task green 후: pkg-01에서 dms 이미지 재빌드(d10)→registry→dms-api·**dms-controller** 재배포
(오케스트레이터 루프는 컨트롤러에 있으므로 controller도 갱신), 테스트베드에서 소형 scan/sync 배치
end-to-end 실증. (슬라이스 1과 동일 절차: rsync→build-and-push→kubectl set image/apply.)
