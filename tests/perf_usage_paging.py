"""사용량 목록 쪽 넘김(2026-11-16, /api/admin/usage/targets 의 offset) 대량 측정 스크립트 -- pytest 가 모으지 않는다(test_ 접두 없음).

테스트베드는 대상이 11개라 수천 경로를 재현할 수 없어(설계 §1.6) 로컬 DB 에 합성 세계를 만들어 잰다. 조건은 routes_usage._WINDOW_LIMIT
주석의 2026-10-20·10-29 측정과 같게 둔다: 한 스토리지 1만 대상 + 6.5만 성공 scan 잡(대상당 평균 6.5회) + 다른 스토리지 2천 대상,
최신 잡의 리포트 요약 캐시는 데운 상태(요약 평균 ~3.6KB -- 지표 정렬이 아티팩트를 열지 않는다), 케이스마다 새 프로세스(첫 호출 시간과
최대 RSS 증가 = HWM). uvicorn 대신 같은 앱을 TestClient 로 부른다(요청 경로·직렬화는 같고 소켓만 없다).

    PYTHONPATH=src python tests/perf_usage_paging.py all --url sqlite:////dev/shm/usage-perf.db
    PYTHONPATH=src:<psycopg 경로> python tests/perf_usage_paging.py all --url 'postgresql://postgres@/db?host=/sock&port=5432'

all = (비어 있으면) 시드 → 케이스(정렬 last_scan·bytes × 1·50·100쪽 + 범위 밖 101쪽)마다 새 프로세스로 run → 표. 기대값(설계): 1쪽 =
옛 값(SQL 정렬 ~0.25초, 지표 정렬 ~1.3~1.4초)이고 뒤 쪽도 같은 급이다(SQL 은 윈도 함수가 파티션 전체를 어차피 계산하고, 지표 정렬은
창 계산이 지배한다)."""
import argparse
import json
import os
import resource
import subprocess
import sys
import tempfile
import time
import uuid

TARGETS, JOBS, OTHER_TARGETS, PAGE = 10_000, 65_000, 2_000, 100
STORAGE, OTHER = "perf-nfs", "perf-other"


def _report(i: int) -> dict:
    """dscan 리포트 모양(시간 축 3개 × 9구간, 크기 10구간) -- 대상마다 값이 달라 지표 정렬이 뜻이 있다."""
    def hist(seed):
        return [{"bucket": f"[{a}d,{b}d]", "min_age_days": a, "max_age_days": b, "bytes": (seed * (k + 3)) % 10_000_019}
                for k, (a, b) in enumerate([(0, 1), (2, 7), (8, 30), (31, 90), (91, 180), (181, 365), (366, 730),
                                            (731, 1825), (1826, 36500)])]
    lims = [4096, 65536, 1048576, 16777216, 268435456, 1073741824, 17179869184, 274877906944, 4398046511104]
    size = [{"bucket": f"[{0 if k == 0 else lims[k - 1] + 1},{lims[k] if k < 9 else 'INF'}]",
             "lower_inclusive": 0 if k == 0 else lims[k - 1] + 1,
             **({"upper_inclusive": lims[k]} if k < 9 else {}), "count": (i * 7 + k) % 997} for k in range(10)]
    return {"generated_at_epoch": 1_790_000_000 + i,
            "summary": {"total_entries": i % 5000 + 10, "total_files": i % 4000 + 5, "total_directories": i % 300 + 1,
                        "total_symlinks": 0, "total_other": 0, "scan_errors": 0, "total_bytes": (i * 7919) % 99_999_989,
                        "invalid_name_entries": 0},
            "time_histograms": {"atime": hist(i + 1), "mtime": hist(i + 2), "ctime": hist(i + 3)},
            "file_size_histogram": size, "broken_paths_total": 0, "broken_paths_limit": 100}


def _insert_jobs(db, rows):
    """(job_id, storage, target, at, bytes) 들을 500 행씩 한 문장으로."""
    from dms.db import dump_json
    wp = dump_json({"identity": {"username": "alice", "uid": os.getuid(), "gid": os.getgid()}})
    for i in range(0, len(rows), 500):
        chunk = rows[i:i + 500]
        values, params = [], {"wp": wp}
        for n, (jid, sn, t, at, b) in enumerate(chunk):
            values.append(f"(:j{n}, :r{n}, 'scan', 'dscan', :sn{n}, :t{n}, '{{}}', 'mid', 'Succeeded', :wp, :b{n}, :at{n}, :at{n})")
            params.update({f"j{n}": jid, f"r{n}": uuid.uuid4().hex, f"sn{n}": sn, f"t{n}": t, f"b{n}": b, f"at{n}": at})
        db.execute("INSERT INTO data_jobs (job_id, request_id, operation, tool, storage_name, target, options, priority, "
                   "state, worker_pool, bytes_count, created_at, updated_at) VALUES " + ", ".join(values), params)


def seed(url: str) -> None:
    from dms.api.routes_usage import _project_report
    from dms.db import Database
    from dms.migrations import migrate
    from dms.repositories import Repositories
    db = Database.connect(url)
    migrate(db)
    if db.query_one("SELECT COUNT(*) AS n FROM data_jobs")["n"] > 0:
        return
    t0 = time.monotonic()
    rows, latest = [], {}
    # 대상 i 의 스캔 횟수 = 6 또는 7(평균 6.5 -- 6.5만 잡), 시각은 대상·회차마다 다르게(최근 스캔 순이 대상 번호와 어긋나게)
    for i in range(TARGETS):
        for k in range(6 + (i % 2)):
            sec = (i * 37 + k * 86_400) % (90 * 86_400)
            at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(1_788_000_000 + sec))
            jid = uuid.uuid4().hex
            rows.append((jid, STORAGE, f"team-{i % 97:02d}/proj-{i:05d}", at, (i * 7919 + k) % 99_999_989))
            if (i not in latest) or at >= latest[i][1]:
                latest[i] = (jid, at)
    for i in range(OTHER_TARGETS):
        for k in range(2):
            at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(1_788_100_000 + i * 11 + k))
            rows.append((uuid.uuid4().hex, OTHER, f"o/{i:05d}", at, i))
    with db.transaction():
        _insert_jobs(db, rows)
    repos = Repositories(db)
    digests = {jid: _project_report(_report(i)) for i, (jid, _) in latest.items()}
    sizes = [len(json.dumps(d)) for d in list(digests.values())[:200]]
    items = list(digests.items())
    for i in range(0, len(items), 2000):
        repos.scan_digests.put_many(dict(items[i:i + 2000]))
    print(f"seeded {len(rows)} jobs ({TARGETS} + {OTHER_TARGETS} targets), digests {len(digests)} "
          f"(~{sum(sizes) / len(sizes) / 1024:.1f} KiB each) in {time.monotonic() - t0:.1f}s", file=sys.stderr)


def run(url: str, sort: str, page: int, art: str) -> dict:
    """새 프로세스 안에서 한 번: 앱·로그인 → 기준 최대 RSS → GET 한 번 → 시간·HWM 증가·행 수·크기."""
    from fastapi.testclient import TestClient
    from dms.api.app import create_app
    from dms.config import Settings
    from dms.db import Database
    db = Database.connect(url)
    settings = Settings(database_url="unused", shared_token="tok-shared", admin_token="tok-admin",
                        session_secret="sess-secret", artifact_base_uri=f"file://{art}")
    client = TestClient(create_app(settings, db))
    if client.app.state.repos.accounts.get("perf") is None:
        client.app.state.repos.accounts.create("perf", "pw", "admin", actor="perf")
    assert client.post("/api/auth/login", json={"username": "perf", "password": "pw"}).status_code == 200
    params = {"storage": STORAGE, "sort": sort, "order": "desc", "limit": PAGE, "offset": (page - 1) * PAGE}
    base = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    t0 = time.perf_counter()
    r = client.get("/api/admin/usage/targets", params=params)
    dt = time.perf_counter() - t0
    assert r.status_code == 200, r.text
    body = r.json()
    hwm = (resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - base) / 1024
    # 최대 RSS 는 앱 기동(임포트·마이그레이션 확인)의 봉우리에 가려 0 으로 읽히기 쉽다 -- 같은 요청을 한 번 더 tracemalloc 아래에서 돌려
    # 요청이 파이썬 힙에 세운 봉우리를 따로 잰다(시간은 위 첫 호출 값).
    import tracemalloc
    tracemalloc.start()
    client.get("/api/admin/usage/targets", params=params)
    py_peak = tracemalloc.get_traced_memory()[1] / 1024 / 1024
    tracemalloc.stop()
    return {"sort": sort, "page": page, "seconds": round(dt, 3), "hwm_mib": round(hwm, 1),
            "py_peak_mib": round(py_peak, 1), "rows": len(body["rows"]), "bytes": len(r.content),
            "matched": body["matched_count"], "pageable": body["pageable_count"], "has_more": body["has_more"]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("seed", "run", "all"))
    ap.add_argument("--url", required=True)
    ap.add_argument("--sort", default="last_scan")
    ap.add_argument("--page", type=int, default=1)
    ap.add_argument("--repeat", type=int, default=2, help="all: 케이스마다 새 프로세스로 몇 번(첫 값·중앙값)")
    a = ap.parse_args()
    art = tempfile.mkdtemp(prefix="usage-perf-art-")
    if a.cmd == "seed":
        seed(a.url)
    elif a.cmd == "run":
        print(json.dumps(run(a.url, a.sort, a.page, art)))
    else:
        seed(a.url)
        cases = [(s, p) for s in ("last_scan", "bytes") for p in (1, 50, 100, 101)]
        print(f"{'sort':>9} {'page':>4} {'seconds':>16} {'rss+ MiB':>10} {'py peak MiB':>12} {'rows':>4} {'KiB':>6} "
              "matched pageable has_more")
        for sort, page in cases:
            outs = []
            for _ in range(a.repeat):
                p = subprocess.run([sys.executable, __file__, "run", "--url", a.url, "--sort", sort, "--page", str(page)],
                                   capture_output=True, text=True, check=True, env=os.environ)
                outs.append(json.loads(p.stdout.strip().splitlines()[-1]))
            o = outs[0]
            secs = "/".join(f"{x['seconds']:.2f}" for x in outs)
            hwms = "/".join(f"{x['hwm_mib']:.0f}" for x in outs)
            peaks = "/".join(f"{x['py_peak_mib']:.1f}" for x in outs)
            print(f"{sort:>9} {page:>4} {secs:>16} {hwms:>10} {peaks:>12} {o['rows']:>4} {o['bytes'] / 1024:>6.0f} "
                  f"{o['matched']} {o['pageable']} {o['has_more']}")


if __name__ == "__main__":
    main()
