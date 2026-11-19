"""배치 단위 삭제 상한 50000 + 청크(백그라운드) 삭제 실측(2026-11-19) -- pytest 가 모으지 않는다(test_ 접두 없음, perf_batch_delete 선례).

테스트베드엔 5만 자식 배치를 만들지 않는다(잡 5만 = 파드 5만 -- 설계 §16). 로컬 DB 에 세계(단건 1만 + 자식 5500 배치 10, 요청·잡만)를
만들고, 측정 배치(자식 50000·항목 5000)는 **현실 모양**으로 만든다: 자식마다 요청 1 + 잡 1 + plan 1 + 결과 1 + 이벤트 1 + 전이(요청 3·
잡 4·plan 1) + scan 리포트 요약 1(조사 B 의 테스트베드 복제본 자식 모양과 같은 행 수 -- 경로·문자열은 합성).

    PYTHONPATH=src:<psycopg> python tests/perf_cap50k.py --url 'postgresql://dms@/dmstest?host=/sock&port=55432' [--n 50000]

재는 것(JSON 한 줄씩 출력):
  api_a        A단계(batch_delete_check -- 트랜잭션 없음) 문장별 시간·최대(= API RLock 을 쥔 가장 긴 문장)
  api_b        B단계(묘비 -- 한 트랜잭션) 시간(= API RLock 을 쥔 시간)
  api_probe    A+B 동안 같은 API 커넥션의 「SELECT 1」(readyz 흉내, 10ms 마다) 최대 대기
  ctl          컨트롤러 BatchDeleter 가 끝까지 지운 벽시계·틱 수·청크 트랜잭션 최대/중앙값
  ctl_probe    그동안 **API 커넥션**(별도 Database = 별도 프로세스 흉내)의 「SELECT 1」 최대 대기 -- 청크는 컨트롤러 커넥션이라 API 와 무관
  rss          단계별 RSS(KiB)
  sync500      동기 경로 500자식(현실 모양) 한 트랜잭션 시간(동기 문턱 기본 500 의 비용)
  audit        감사 행 수·바이트(start + chunk + delete)
  outbox_due   정리 아웃박스 due(limit 20) 문장 시간(방금 넣은 5만 행 레인 -- 컨트롤러 request-purge 틱)
  preview      확인 창 미리보기 라우트(delete_preview) 최악 -- 자식 --preview-n 배치 --preview-batches 개(기본 5만 × 10)를 고른
               호출의 문장 최대(= API RLock 을 쥔 가장 긴 문장)·벽시계·같은 커넥션 readyz 흉내 대기(10·2·1개 선택, 각 2회)
PG 는 일회용 스키마를 만들고 끝에 지운다(--keep 이면 남긴다)."""
import argparse
import json
import os
import statistics
import threading
import time
import uuid

OLD = "2026-01-01T00:00:00Z"


def _connect(url, schema):
    from dms.db import Database
    if url.startswith("postgresql") and schema:
        return Database.connect(f"{url}{'&' if '?' in url else '?'}options=-csearch_path%3D{schema}")
    return Database.connect(url)


def _rss_kb():
    with open("/proc/self/statm") as f:
        return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") // 1024


def _emit(tag, **kw):
    print(json.dumps({"tag": tag, **kw}, ensure_ascii=False), flush=True)


def _simple(db, bid, n, start):
    """세계용 자식(요청 + 성공 scan 잡) -- 500행 묶음."""
    for i in range(0, n, 500):
        rv, jv, rp, jp = [], [], {"at": OLD, "b": bid}, {"at": OLD}
        for k in range(i, min(n, i + 500)):
            rid, jid = uuid.uuid4().hex, uuid.uuid4().hex
            rv.append(f"(:r{k}, :o{k}, 'scan', 'ops', 'ops', :k{k}, 'mid', '{{}}', 'Succeeded', :at, :at, :b, 'session')")
            rp.update({f"r{k}": rid, f"o{k}": start + k, f"k{k}": f"k{start + k}"})
            jv.append(f"(:j{k}, :r{k}, 'scan', 'dscan', 's1', :t{k}, '{{}}', 'mid', 'Succeeded', :at, :at)")
            jp.update({f"j{k}": jid, f"r{k}": rid, f"t{k}": f"t{start + k}"})
        db.execute("INSERT INTO requests (request_id, commit_order, operation, requester_id, actor, resource_key, priority, "
                   "payload, state, created_at, updated_at, batch_id, auth_method) VALUES " + ", ".join(rv), rp)
        db.execute("INSERT INTO data_jobs (job_id, request_id, operation, tool, storage_name, target, options, priority, "
                   "state, created_at, updated_at) VALUES " + ", ".join(jv), jp)
    return start + n


def _realistic(db, bid, n, start):
    """측정 배치 자식(현실 모양 -- 모듈 docstring). 250개 묶음."""
    payload = json.dumps({"storage": "gpfs-proj01", "target": "/gpfs/proj01/team00/user00000/dataset",
                          "options": {"depth": 3}})
    phase = json.dumps({"preflight": "pod/dms-scan-preflight-x", "execution": "vcjob/dms-scan-execution-x"})
    summary = json.dumps({"files": 123456, "bytes": 987654321, "dirs": 4321})
    for i in range(0, n, 250):
        rows = {t: [] for t in ("requests", "data_jobs", "plans", "results", "events", "st", "srd")}
        p = {"at": OLD, "b": bid, "pl": payload, "ph": phase, "sm": summary}
        for k in range(i, min(n, i + 250)):
            rid, jid, pid = uuid.uuid4().hex, uuid.uuid4().hex, uuid.uuid4().hex
            p.update({f"r{k}": rid, f"j{k}": jid, f"p{k}": pid, f"o{k}": start + k})
            rows["requests"].append(f"(:r{k}, :o{k}, 'scan', 'ops', 'ops', :r{k}, 'mid', :pl, 'Succeeded', :at, :at, :b, 'session')")
            rows["data_jobs"].append(f"(:j{k}, :r{k}, 'scan', 'dscan', 'gpfs-proj01', :r{k}, '{{}}', 'mid', 'Succeeded', :at, :at, "
                                     f":ph, 123456, 987654321, 'file:///base/x')")
            rows["plans"].append(f"(:p{k}, :r{k}, :j{k}, 'Planned', :at, :at)")
            rows["results"].append(f"(:r{k}, 'Succeeded', NULL, 'scan finished', :sm, :at)")
            rows["events"].append(f"(:r{k}, 'stepper', 'info', 'job_succeeded', 'scan finished', NULL, :at)")
            for a, b in ((None, "Pending"), ("Pending", "Planned"), ("Planned", "Succeeded")):
                rows["st"].append(f"('request', :r{k}, {('NULL' if a is None else repr(a))}, '{b}', NULL, 'planner', :at)")
            for a, b in ((None, "Previewing"), ("Previewing", "Submitted"), ("Submitted", "Running"), ("Running", "Succeeded")):
                rows["st"].append(f"('data_job', :j{k}, {('NULL' if a is None else repr(a))}, '{b}', NULL, 'stepper', :at)")
            rows["st"].append(f"('plan', :p{k}, NULL, 'Planned', NULL, 'planner', :at)")
            rows["srd"].append(f"(:j{k}, :sm, :at)")
        db.execute("INSERT INTO requests (request_id, commit_order, operation, requester_id, actor, resource_key, priority, "
                   "payload, state, created_at, updated_at, batch_id, auth_method) VALUES " + ", ".join(rows["requests"]), p)
        db.execute("INSERT INTO data_jobs (job_id, request_id, operation, tool, storage_name, target, options, priority, state, "
                   "created_at, updated_at, phase_refs, files_count, bytes_count, artifact_uri) VALUES "
                   + ", ".join(rows["data_jobs"]), p)
        db.execute("INSERT INTO plans (plan_id, request_id, job_id, state, created_at, updated_at) VALUES "
                   + ", ".join(rows["plans"]), p)
        db.execute("INSERT INTO results (request_id, terminal_state, reason_code, message, summary, completed_at) VALUES "
                   + ", ".join(rows["results"]), p)
        db.execute("INSERT INTO events (request_id, component, severity, event_type, message, payload, at) VALUES "
                   + ", ".join(rows["events"]), p)
        db.execute("INSERT INTO state_transitions (entity_kind, entity_id, from_state, to_state, reason_code, actor, at) VALUES "
                   + ", ".join(rows["st"]), p)
        db.execute("INSERT INTO scan_report_digests (job_id, digest, created_at) VALUES " + ", ".join(rows["srd"]), p)
    return start + n


def _batch(repos, n_items):
    items = [{"storage": "gpfs-proj01", "target": f"/gpfs/proj01/team{i % 50:02d}/user{i:05d}/dataset"}
             for i in range(n_items)]
    return repos.batches.create(operation="scan", requester_id="ops", actor="ops", max_concurrency=4, options={},
                                note="perf cap50k", items=items, status="Completed", name="perf-cap50k",
                                auth_method="session")


class _Timed:
    """Database 프록시 -- 문장마다(=RLock 을 잡는 단위) 시간을 잰다. transaction 은 그 블록 전체를 잰다."""

    def __init__(self, db):
        self._db = db
        self.dialect = db.dialect
        self.stmts: list[tuple[float, str]] = []
        self.txns: list[float] = []
        self._in_txn = False

    def __getattr__(self, name):
        return getattr(self._db, name)

    def _wrap(self, fn, sql, *a, **k):
        t = time.perf_counter()
        try:
            return fn(sql, *a, **k)
        finally:
            if not self._in_txn:
                self.stmts.append((time.perf_counter() - t, " ".join(sql.split())[:70]))

    def query(self, sql, *a, **k):
        return self._wrap(self._db.query, sql, *a, **k)

    def query_one(self, sql, *a, **k):
        return self._wrap(self._db.query_one, sql, *a, **k)

    def execute(self, sql, *a, **k):
        return self._wrap(self._db.execute, sql, *a, **k)

    def execute_count(self, sql, *a, **k):
        return self._wrap(self._db.execute_count, sql, *a, **k)

    def transaction(self):
        outer = self

        class _Ctx:
            def __enter__(self):
                self._cm = outer._db.transaction()
                self._cm.__enter__()
                outer._in_txn = True
                self._t = time.perf_counter()

            def __exit__(self, *exc):
                outer._in_txn = False
                outer.txns.append(time.perf_counter() - self._t)
                return self._cm.__exit__(*exc)
        return _Ctx()


class _Probe:
    """다른 스레드에서 db.query_one('SELECT 1') 을 period 마다 -- 대기 시간(호출 지연)의 최대·p99."""

    def __init__(self, db, period=0.01):
        self._db, self._period = db, period
        self.waits: list[float] = []
        self._stop = threading.Event()
        self._th = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.is_set():
            t = time.perf_counter()
            self._db.query_one("SELECT 1 AS x")
            self.waits.append(time.perf_counter() - t)
            time.sleep(self._period)

    def __enter__(self):
        self._th.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._th.join()

    def stats(self):
        w = sorted(self.waits)
        return {"probes": len(w), "max_ms": round(w[-1] * 1000, 1) if w else None,
                "p99_ms": round(w[int(len(w) * 0.99) - 1] * 1000, 1) if len(w) >= 100 else None}


def _preview_stage(api, repos, settings, a, nxt, pg):
    """확인 창 미리보기(POST /api/admin/requests:delete-preview) 최악 -- 자식 a.preview_n 배치 a.preview_batches 개를 더 만들고
    **라우트 함수 그대로**(가짜 request) 부른다. 문장별 시간(= API RLock 을 쥔 단위)의 최대·벽시계·같은 커넥션 readyz 흉내 대기."""
    from types import SimpleNamespace
    from dms.api.routes_request_purge import DeletePreviewBody, delete_preview
    from dms.repositories import Repositories
    t0 = time.perf_counter()
    bids = []
    for _ in range(a.preview_batches):
        b = _batch(repos, 10)
        nxt = _simple(api, b, a.preview_n, nxt)
        bids.append(b)
    if pg:
        api.execute("ANALYZE")
    _emit("preview_seed", seconds=round(time.perf_counter() - t0, 1), batches=a.preview_batches, n=a.preview_n,
          requests=api.query_one("SELECT COUNT(*) AS n FROM requests")["n"])
    for k in sorted({a.preview_batches, 2, 1}, reverse=True):
        for rep in range(2):
            timed = _Timed(api)
            req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(repos=Repositories(timed), settings=settings)))
            with _Probe(api) as probe:
                tw = time.perf_counter()
                out = delete_preview(DeletePreviewBody(batch_ids=bids[:k]), req, None)
                wall = time.perf_counter() - tw
            top = sorted(timed.stmts, reverse=True)[:3]
            _emit("preview", batches=k, rep=rep, wall_s=round(wall, 3), stmts=len(timed.stmts),
                  max_stmt_ms=round(top[0][0] * 1000, 1), top3=[[round(s * 1000, 1), q[:60]] for s, q in top],
                  verdicts=sorted({x["verdict"] for x in out["batches"]}),
                  **{f"probe_{kk}": v for kk, v in probe.stats().items()})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--n", type=int, default=50000)
    ap.add_argument("--items", type=int, default=5000)
    ap.add_argument("--singles", type=int, default=10000)
    ap.add_argument("--batches", type=int, default=10)
    ap.add_argument("--per-batch", type=int, default=5500)
    ap.add_argument("--preview-batches", type=int, default=10)
    ap.add_argument("--preview-n", type=int, default=50000)
    a = ap.parse_args()
    from dms.batch_deleter import BatchDeleter
    from dms.config import Settings
    from dms.db import utc_now_iso
    from dms.migrations import migrate
    from dms.repositories import Repositories
    from dms.repositories.request_purges import RequestPurgesRepository
    pg = a.url.startswith("postgresql")
    schema = None
    if pg:
        import psycopg
        schema = "perf_c50_" + uuid.uuid4().hex[:10]
        with psycopg.connect(a.url, autocommit=True) as c:
            c.execute(f"CREATE SCHEMA {schema}")
    api = _connect(a.url, schema)
    ctl = None
    try:
        migrate(api)
        repos = Repositories(api)
        repos.accounts.create("ops", "p", "admin", actor="t")
        t0 = time.perf_counter()
        nxt = _simple(api, None, a.singles, 1)
        for _ in range(a.batches):
            nxt = _simple(api, _batch(repos, 10), a.per_batch, nxt)
        bid = _batch(repos, a.items)
        nxt = _realistic(api, bid, a.n, nxt)
        small = _batch(repos, 5)
        nxt = _realistic(api, small, 500, nxt)
        if pg:
            api.execute("ANALYZE")
        _emit("seed", seconds=round(time.perf_counter() - t0, 1), n=a.n, items=a.items,
              requests=api.query_one("SELECT COUNT(*) AS n FROM requests")["n"],
              transitions=api.query_one("SELECT COUNT(*) AS n FROM state_transitions")["n"])
        settings = Settings(database_url="x", shared_token="t", admin_token="a", session_secret="s")
        # 동기 500(현실 모양) -- 동기 문턱 기본값의 한 트랜잭션 비용.
        timed = _Timed(api)
        rss0 = _rss_kb()
        t = time.perf_counter()
        r = RequestPurgesRepository(timed).delete_batch(small, expected_request_count=500, actor="ops", artifact_base="/base",
                                                         quiet_seconds=60, max_children=50000)
        _emit("sync500", deleted=r["deleted"], seconds=round(time.perf_counter() - t, 3),
              txn_seconds=round(max(timed.txns), 3), rss_delta_kb=_rss_kb() - rss0)
        # API: A단계 + B단계(같은 API 커넥션의 readyz 흉내가 재는 동안).
        rss_api0 = _rss_kb()
        timed = _Timed(api)
        rp = RequestPurgesRepository(timed)
        with _Probe(api) as probe:
            ta = time.perf_counter()
            chk = rp.batch_delete_check(bid, cap=50000, quiet_seconds=60, expected_request_count=a.n)
            a_wall = time.perf_counter() - ta
            a_stmts = list(timed.stmts)
            tb = time.perf_counter()
            res = rp._begin_batch_delete(bid, expected_request_count=a.n, expected_max_commit_order=None, actor="ops",
                                         cap=50000, max_items=10000, quiet_seconds=60, sync_threshold=500,
                                         now=utc_now_iso(),
                                         scan_storage=None)
            b_wall = time.perf_counter() - tb
        _emit("api_a", reason=chk["reason"], wall_s=round(a_wall, 3),
              max_stmt_ms=round(max(s for s, _ in a_stmts) * 1000, 1),
              stmts=[[round(s * 1000, 1), q] for s, q in a_stmts])
        _emit("api_b", result={k: res.get(k) for k in ("deleting", "already", "request_count")},
              txn_ms=round(timed.txns[-1] * 1000, 1), wall_ms=round(b_wall * 1000, 1))
        _emit("api_probe", **probe.stats(), rss_delta_kb=_rss_kb() - rss_api0)
        # 컨트롤러: 별도 Database(= 별도 프로세스의 커넥션 흉내)로 끝까지. 그동안 API 커넥션의 readyz 흉내.
        ctl = _connect(a.url, schema)
        timed_ctl = _Timed(ctl)
        ctl_repos = Repositories(timed_ctl)
        rss_ctl0 = _rss_kb()
        ticks = 0
        tc = time.perf_counter()
        with _Probe(api) as probe:
            while api.query_one("SELECT 1 AS x FROM batch_deletions WHERE batch_id = :b", {"b": bid}) is not None:
                BatchDeleter(ctl_repos, settings=settings).run_once()
                ticks += 1
                if ticks > 2000:
                    raise RuntimeError("did not finish")
        c_wall = time.perf_counter() - tc
        txns = sorted(timed_ctl.txns)
        _emit("ctl", wall_s=round(c_wall, 2), ticks=ticks, txns=len(txns), txn_max_ms=round(txns[-1] * 1000, 1),
              txn_median_ms=round(statistics.median(txns) * 1000, 1),
              left=api.query_one("SELECT COUNT(*) AS n FROM requests WHERE batch_id = :b", {"b": bid})["n"])
        _emit("ctl_probe", **probe.stats(), rss_delta_kb=_rss_kb() - rss_ctl0)
        rows = api.query("SELECT operation, LENGTH(before_state) AS n FROM audit_log WHERE target_key = :b", {"b": bid})
        by = {}
        for x in rows:
            by.setdefault(x["operation"], [0, 0])
            by[x["operation"]][0] += 1
            by[x["operation"]][1] += int(x["n"] or 0)
        _emit("audit", rows={k: v[0] for k, v in by.items()}, bytes={k: v[1] for k, v in by.items()},
              outbox=api.query_one("SELECT COUNT(*) AS n FROM request_purges WHERE purge_group = :b", {"b": bid})["n"])
        # 정리 아웃박스 due()(컨트롤러 request-purge 틱 -- 방금 넣은 5만 행 레인 + NULL 레인).
        rp_plain = RequestPurgesRepository(api)
        due_ms = []
        for _ in range(3):
            t = time.perf_counter()
            rp_plain.due("2999-01-01T00:00:00Z", limit=20, skip_stages=("purging",))
            due_ms.append(round((time.perf_counter() - t) * 1000, 1))
        _emit("outbox_due", rows=api.query_one("SELECT COUNT(*) AS n FROM request_purges")["n"], due_ms=due_ms)
        if a.preview_batches > 0:
            _preview_stage(api, repos, settings, a, nxt, pg)
        _emit("rss", now_kb=_rss_kb())
    finally:
        if ctl is not None and pg:
            ctl._conn.close()
        if pg and not a.keep:
            api._conn.close()
            import psycopg
            with psycopg.connect(a.url, autocommit=True) as c:
                c.execute(f"DROP SCHEMA {schema} CASCADE")


if __name__ == "__main__":
    main()
