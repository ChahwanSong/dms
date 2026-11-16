"""배치 단위 삭제 상한 5000·회차 보존 정리 측정 스크립트(2026-11-16) -- pytest 가 모으지 않는다(test_ 접두 없음, perf_usage_paging 선례).

테스트베드엔 자식 5000 배치를 만들지 않는다(설계 §11-8) -- 로컬 DB 에 합성 세계를 만들어 잰다. 세계(조사 B 와 같은 분포): 단건 1만 +
자식 5500 배치 20(자식마다 성공 scan 잡 1) = 요청·잡 ~12만, 그 위에 측정 배치를 케이스마다 새로 만든다.

    PYTHONPATH=src python tests/perf_batch_delete.py --url sqlite:////dev/shm/bd-perf.db
    PYTHONPATH=src:<psycopg 경로> python tests/perf_batch_delete.py --url 'postgresql://dms@/dmstest?host=/sock&port=55432'

재는 것:
  1. delete_batch 자식 2000 / 5000(잡 각 1) 한 트랜잭션 시간
  2. 5000 배치 2개를 연달아 지우는 동안 다른 스레드의 「SELECT 1」(readyz 흉내, 50ms 마다) 최대 대기 -- 단일 커넥션 RLock
  3. list_summaries 성공 scan 갈래(cap 5000)의 자식 4000/4500/5000 배치 시간 -- 옛 EXISTS 꼴 vs 새 스칼라 서브쿼리 꼴(PG 는 EXPLAIN
     의 계획 모양도)
  4. 회차 보존 정리 청크 100(prune_round_children) 시간 -- 자식이 정확히 100개인 배치(최선 경우)
  5. 같은 청크를 현실적인 대기량에서(2026-11-16 검증 지적): 자식 3만 배치의 경계 이하 2.5만(커서를 이어 5청크 중앙값), 그리고
     앞 2만이 막힌(요청 비종단) 경우 -- 고르기가 창·예산에 묶여 청크 비용이 대기량에 비례하지 않는지
PG 는 일회용 스키마를 만들고 끝에 지운다(--keep 이면 남긴다)."""
import argparse
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


def _bulk(db, bid, n, *, start):
    """자식 n(성공 scan 잡 각 1)을 500행 묶음 INSERT 로. commit_order = start.. 를 쓴다."""
    for i in range(0, n, 500):
        req_vals, job_vals, rp, jp = [], [], {"at": OLD, "b": bid}, {"at": OLD}
        for k in range(i, min(n, i + 500)):
            rid, jid = uuid.uuid4().hex, uuid.uuid4().hex
            req_vals.append(f"(:r{k}, :o{k}, 'scan', 'ops', 'ops', :k{k}, 'mid', '{{}}', 'Succeeded', :at, :at, :b, 'session')")
            rp.update({f"r{k}": rid, f"o{k}": start + k, f"k{k}": f"k{start + k}"})
            job_vals.append(f"(:j{k}, :r{k}, 'scan', 'dscan', 's1', :t{k}, '{{}}', 'mid', 'Succeeded', :at, :at)")
            jp.update({f"j{k}": jid, f"r{k}": rid, f"t{k}": f"t{start + k}"})
        db.execute("INSERT INTO requests (request_id, commit_order, operation, requester_id, actor, resource_key, priority, "
                   "payload, state, created_at, updated_at, batch_id, auth_method) VALUES " + ", ".join(req_vals), rp)
        db.execute("INSERT INTO data_jobs (job_id, request_id, operation, tool, storage_name, target, options, priority, "
                   "state, created_at, updated_at) VALUES " + ", ".join(job_vals), jp)
    return start + n


def _batch(repos, n_items=1):
    return repos.batches.create(operation="scan", requester_id="ops", actor="ops", max_concurrency=1, options={},
                                note=None, items=[{"storage": "s1", "target": f"t{i}"} for i in range(n_items)],
                                status="Completed", name="perf", auth_method="session")


def _summary_arm(db, bid, *, cap, scalar):
    from dms.repositories.data_jobs import DataJobsRepository
    from dms.repositories.requests import batch_match
    params = {"j0": bid, "stop": cap + 1}
    where = DataJobsRepository._scan_target_where(params)
    judge = (f"(SELECT 1 FROM data_jobs WHERE data_jobs.request_id = c0.request_id AND {where} LIMIT 1) IS NOT NULL"
             if scalar else f"EXISTS (SELECT 1 FROM data_jobs WHERE data_jobs.request_id = c0.request_id AND {where})")
    sql = (f"SELECT batch_id, COUNT(*) AS n FROM (SELECT batch_id, request_id FROM requests WHERE {batch_match('j0')} "
           f"ORDER BY batch_id, commit_order LIMIT :stop) c0 WHERE {judge} GROUP BY batch_id")
    return sql, params


def _time(fn, reps=1):
    out = []
    for _ in range(reps):
        t = time.perf_counter()
        fn()
        out.append(time.perf_counter() - t)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--batches", type=int, default=20)
    ap.add_argument("--per-batch", type=int, default=5500)
    ap.add_argument("--singles", type=int, default=10000)
    a = ap.parse_args()
    from dms.migrations import migrate
    from dms.repositories import Repositories
    from dms.repositories.request_purges import RequestPurgesRepository
    from dms.batch_scheduler import SCHEDULER_ACTOR
    pg = a.url.startswith("postgresql")
    schema = None
    if pg:
        import psycopg
        schema = "perf_bd_" + uuid.uuid4().hex[:10]
        with psycopg.connect(a.url, autocommit=True) as c:
            c.execute(f"CREATE SCHEMA {schema}")
    db = _connect(a.url, schema)
    try:
        migrate(db)
        repos = Repositories(db)
        repos.accounts.create("ops", "p", "admin", actor="t")
        t0 = time.perf_counter()
        nxt = _bulk(db, None, a.singles, start=1)
        for _ in range(a.batches):
            nxt = _bulk(db, _batch(repos), a.per_batch, start=nxt)
        if pg:
            db.execute("ANALYZE")
        print(f"world: singles {a.singles} + {a.batches} x {a.per_batch} children "
              f"({db.query_one('SELECT COUNT(*) AS n FROM data_jobs')['n']} jobs) seeded in {time.perf_counter() - t0:.1f}s")

        # 3. list_summaries 성공 scan 갈래
        for n in (4000, 4500, 5000):
            bid = _batch(repos)
            nxt = _bulk(db, bid, n, start=nxt)
            if pg:
                db.execute("ANALYZE requests")
            for scalar in (False, True):
                sql, params = _summary_arm(db, bid, cap=5000, scalar=scalar)
                db.query(sql, params)                           # 데우기
                ts = _time(lambda: db.query(sql, params), reps=5)
                plan = ""
                if pg:
                    rows = db.query("EXPLAIN " + sql, params)
                    text = " | ".join(next(iter(r.values())) for r in rows)
                    plan = ("HashSemiJoin+SeqScan" if "Hash Semi Join" in text or "Seq Scan on data_jobs" in text
                            else "SubPlan+IndexScan" if "SubPlan" in text else "NestedLoop")
                print(f"summary arm children={n} {'scalar' if scalar else 'EXISTS'}: median "
                      f"{statistics.median(ts) * 1000:.1f}ms {plan}")
            full = _time(lambda: repos.batches.list_summaries([bid], cap=5000), reps=3)
            print(f"list_summaries([one], cap=5000) children={n}: median {statistics.median(full) * 1000:.1f}ms")

        # 1. delete_batch 2000 / 5000
        purges = RequestPurgesRepository(db)
        for n in (2000, 5000):
            bid = _batch(repos)
            nxt = _bulk(db, bid, n, start=nxt)
            t = time.perf_counter()
            r = purges.delete_batch(bid, expected_request_count=n, actor="ops", artifact_base="/x", quiet_seconds=60,
                                    max_children=5000)
            assert r["deleted"], r
            print(f"delete_batch children={n}: {time.perf_counter() - t:.2f}s")

        # 2. 5000 x 2 연속 + readyz 흉내 최대 대기
        bids = []
        for _ in range(2):
            bid = _batch(repos)
            nxt = _bulk(db, bid, 5000, start=nxt)
            bids.append(bid)
        waits, stop = [], threading.Event()

        def probe():
            while not stop.is_set():
                t = time.perf_counter()
                db.query_one("SELECT 1 AS x")
                waits.append(time.perf_counter() - t)
                time.sleep(0.05)
        th = threading.Thread(target=probe)
        th.start()
        t = time.perf_counter()
        for bid in bids:
            assert purges.delete_batch(bid, expected_request_count=5000, actor="ops", artifact_base="/x",
                                       quiet_seconds=60, max_children=5000)["deleted"]
        total = time.perf_counter() - t
        stop.set()
        th.join()
        print(f"2 x 5000 consecutive: {total:.2f}s, probe max wait {max(waits):.2f}s over {len(waits)} probes")

        # 4. 정리 청크 100
        bid = _batch(repos)
        start = nxt
        nxt = _bulk(db, bid, 100, start=nxt)
        repos.batch_schedules.create(batch_id=bid, kind="daily", spec={"hour": 2, "minute": 0}, enabled=False,
                                     next_run_at=None, actor="ops", arm=lambda b, i: None)
        t = time.perf_counter()
        r = purges.prune_round_children(bid, upto_order=start + 99, limit=100, actor=SCHEDULER_ACTOR,
                                        artifact_base="/x", quiet_seconds=60)
        assert len(r["deleted"]) == 100, r
        print(f"prune chunk 100: {(time.perf_counter() - t) * 1000:.0f}ms")

        # 5. 정리 청크 100 -- 경계 이하 대기 2.5만, 그리고 앞 2만이 막힌 경우
        for blocked in (0, 20000):
            bid = _batch(repos)
            start = nxt
            nxt = _bulk(db, bid, 30000, start=nxt)
            if blocked:
                db.execute("UPDATE requests SET state = 'Running' WHERE batch_id = :b AND commit_order < :c",
                           {"b": bid, "c": start + blocked})
            repos.batch_schedules.create(batch_id=bid, kind="daily", spec={"hour": 2, "minute": 0}, enabled=False,
                                         next_run_at=None, actor="ops", arm=lambda b, i: None)
            if pg:
                db.execute("ANALYZE")
            after, ts, gone = None, [], 0
            for _ in range(8 if blocked else 5):
                t = time.perf_counter()
                r = purges.prune_round_children(bid, upto_order=start + 25000, limit=100, actor=SCHEDULER_ACTOR,
                                                artifact_base="/x", quiet_seconds=60, scan_after=after)
                ts.append(time.perf_counter() - t)
                after, gone = r["scan_next"], gone + len(r["deleted"])
            print(f"prune chunk 100, backlog 25000{f' (first {blocked} blocked)' if blocked else ''}: "
                  f"median {statistics.median(ts) * 1000:.0f}ms max {max(ts) * 1000:.0f}ms, deleted {gone} in {len(ts)} calls")
    finally:
        db._conn.close()
        if pg and not a.keep:
            import psycopg
            with psycopg.connect(a.url, autocommit=True) as c:
                c.execute(f"DROP SCHEMA {schema} CASCADE")


if __name__ == "__main__":
    main()
