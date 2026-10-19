"""사용량 분석 목록·내보내기 재구성(2026-10-20)의 동일성 그물 -- 옛 구현을 이 모듈 안에 **참조로 박제**해, 같은 DB·같은
입력에서 새 라우트와 응답 바이트를 비교한다.

무엇이 바뀌었나(routes_usage 모듈 docstring): 머리 질의 한 문장(타깃별 최신 잡 -- ROW_NUMBER/COUNT/MIN OVER), 지표 정렬은
창의 정렬 값만 묶음으로 계산(포인트는 반환 행만), 최근 잡은 (storage, target) OR 300쌍 대신 스토리지별 `target IN (…)`,
캐시 쓰기는 put_many, 내보내기는 500 타깃 묶음 스트리밍. 응답은 바뀌면 안 된다 -- 포탈·CSV·스크립트가 그 모양을 읽는다.

비교 규칙:
- 목록: 바이트가 같다. 참조도 같은 앱에 라우트로 붙여 같은 FastAPI 직렬화를 거친다.
- 내보내기: 행 바이트가 같다. 본문 키 순서만 설계대로 {generated_at, truncated, rows, count}(count 가 맨 뒤 -- 다 흘린
  뒤에야 정해진다) 이고 generated_at 은 시각이라, 참조 본문의 rows 바이트를 그대로 잘라 새 순서로 다시 맞춘 것과 비교한다.
- 캐시 상태도 입력이다. 리포트 요약 캐시는 dump_json(sort_keys)라, 옛 구현부터 **캐시가 빈 첫 호출**(리포트를 막 읽은
  투영 -- 리포트의 키 순서)과 **그 뒤 호출**(캐시 -- 정렬된 키 순서)의 summary·time_histograms 안 키 순서가 달랐다(값은 같음).
  그래서 데운 캐시에서는 모든 정렬이 바이트 동일, 빈 캐시에서는 SQL 정렬(last_scan·scans)이 바이트 동일이다. 빈 캐시의 지표
  정렬은 새 구현이 정렬 값을 계산하며 캐시를 먼저 채우므로 응답이 **데운 캐시 형태**다 -- 옛 구현의 데운 캐시 응답과 바이트가
  같고, 옛 구현의 빈 캐시 응답과는 키 순서만 다르다(정규화 비교 + 행 순서 동일).

PostgreSQL: DMS_TEST_PG_URL(일회용 서버)을 주면 같은 비교를 PG 에서도 돈다 -- 테스트마다 새 스키마를 만들고 지운다(그
URL 의 다른 스키마는 건드리지 않는다). 없으면 그 매개변수만 건너뛴다. 맨 아래의 두 방언 테스트(put_many 의 VALUES …
ON CONFLICT, 탭 요약 GROUP BY)도 같은 픽스처를 쓴다. 2026-10-20 에 postgres:16 일회용 컨테이너로 전부 통과를 확인했다."""
import json
import os
import uuid

import pytest
from fastapi import Query, Request
from fastapi.responses import Response
from fastapi.testclient import TestClient

from dms.api import routes_usage as ru
from dms.config import Settings
from dms.db import Database, utc_now_iso
from dms.domain import DataJobState
from dms.migrations import migrate
from dms.repositories import data_jobs as data_jobs_mod
from dms.repositories.data_jobs import DataJobsRepository, _ROW_COLUMNS_SANS_DIAG
from dms.usage_metrics import METRIC_SORTS, SORT_PATTERN, metric_value

SORTS = ("last_scan", "scans", "bytes", "files", "hot")


# ======================================================================================================================
# 참조: 8c8f73e 의 구현(routes_usage.scan_targets·export_usage·_with_latest·_digests, DataJobsRepository.scan_targets·
# _recent_scans 의 쌍 경로, ScanDigestsRepository.put, usage_metrics.sort_by_metric) -- 고치지 말 것. 바뀌지 않은 공용 조각
# (_scan_target_where·_project_report·_read_digest·_point·_previous·_storage_infos·get_many·_hydrate)은 그대로 부른다.
# ======================================================================================================================

def _ref_scan_targets(db, *, q=None, storage=None, path=None, order="desc", limit=100, sort="last_scan"):
    params: dict = {}
    where = DataJobsRepository._scan_target_where(params, q=q, storage=storage, path=path)
    direction = "ASC" if order == "asc" else "DESC"
    order_by = (f"scan_count {direction}, last_scan_at DESC" if sort == "scans"
                else f"last_scan_at {direction}")
    tail = ""
    if limit is not None:
        tail = " LIMIT :n"
        params["n"] = limit
    return db.query(
        f"""SELECT storage_name, target, COUNT(*) AS scan_count,
                   MIN(updated_at) AS first_scan_at,
                   MAX(updated_at) AS last_scan_at
            FROM data_jobs WHERE {where}
            GROUP BY storage_name, target
            ORDER BY {order_by}, storage_name, target{tail}""",
        params)


def _ref_recent_scans_by_target(repos, *, per_target, pairs):
    out: dict = {}
    for i in range(0, len(pairs), 300):
        chunk = pairs[i:i + 300]
        params: dict = {"k": per_target}
        where = DataJobsRepository._scan_target_where(params)
        ors = []
        for n, (sn, t) in enumerate(chunk):
            ors.append(f"(storage_name = :ps{n} AND target = :pt{n})")
            params[f"ps{n}"], params[f"pt{n}"] = sn, t
        where += f" AND ({' OR '.join(ors)})"
        rows = repos.db.query(
            f"""SELECT * FROM (
                    SELECT {_ROW_COLUMNS_SANS_DIAG},
                           ROW_NUMBER() OVER (PARTITION BY storage_name, target
                                              ORDER BY updated_at DESC, job_id DESC) AS rn
                    FROM data_jobs WHERE {where}) ranked
                WHERE rn <= :k ORDER BY storage_name, target, rn""",
            params)
        for r in rows:
            r.pop("rn", None)
            job = repos.data_jobs._hydrate(r)
            out.setdefault((job["storage_name"], job["target"]), []).append(job)
    return out


def _ref_put(db, job_id, digest):
    from dms.db import dump_json
    db.execute(
        """INSERT INTO scan_report_digests (job_id, digest, created_at)
           SELECT :j, :d, :now WHERE EXISTS (SELECT 1 FROM data_jobs WHERE job_id = :j)
           ON CONFLICT (job_id) DO NOTHING""",
        {"j": job_id, "d": dump_json(digest), "now": utc_now_iso()})


def _ref_digests(repos, base, jobs):
    cached = {jid: ru._project_report(d)
              for jid, d in repos.scan_digests.get_many([j["job_id"] for j in jobs]).items()}
    for job in jobs:
        if job["job_id"] in cached:
            continue
        digest = ru._read_digest(base, job)
        if digest is None:
            continue
        _ref_put(repos.db, job["job_id"], digest)
        cached[job["job_id"]] = digest
    return cached


def _ref_sort_by_metric(rows, sort, order):
    known = [r for r in rows if metric_value(r, sort) is not None]
    unknown = [r for r in rows if metric_value(r, sort) is None]
    tie = lambda r: (r.get("storage_name") or "", r.get("target") or "")   # noqa: E731
    for group in (known, unknown):
        group.sort(key=tie)
        group.sort(key=lambda r: r.get("last_scan_at") or "", reverse=True)
    known.sort(key=lambda r: metric_value(r, sort), reverse=(order == "desc"))
    return known + unknown


def _ref_with_latest(request, rows):
    repos = request.app.state.repos
    recent = _ref_recent_scans_by_target(repos, per_target=1, pairs=[(r["storage_name"], r["target"]) for r in rows])
    latest_jobs = [jobs[0] for jobs in recent.values()]
    digests = _ref_digests(repos, ru._base(request), latest_jobs)
    for r in rows:
        jobs = recent.get((r["storage_name"], r["target"])) or []
        r["latest"] = ru._point(jobs[0], digests.get(jobs[0]["job_id"])) if jobs else None
    return rows


def ref_scan_targets(request: Request, q: "str | None" = Query(None, max_length=512),
                     storage: "str | None" = Query(None, max_length=512),
                     path: "str | None" = Query(None, max_length=512),
                     order: str = Query("desc", pattern="^(asc|desc)$"),
                     limit: int = Query(100, ge=1, le=200),
                     sort: str = Query("last_scan", pattern=SORT_PATTERN)):
    db = request.app.state.repos.db
    if sort in METRIC_SORTS:
        window = _ref_scan_targets(db, q=q or None, storage=storage or None, path=path or None,
                                   order="desc", limit=ru._WINDOW_LIMIT)
        return _ref_sort_by_metric(_ref_with_latest(request, window), sort, order)[:limit]
    rows = _ref_scan_targets(db, q=q or None, storage=storage or None,
                             path=path or None, order=order, limit=limit, sort=sort)
    return _ref_with_latest(request, rows)


def ref_export_usage(request: Request, q: "str | None" = Query(None, max_length=512),
                     storage: "str | None" = Query(None, max_length=512),
                     path: "str | None" = Query(None, max_length=512),
                     order: str = Query("desc", pattern="^(asc|desc)$"),
                     sort: str = Query("last_scan", pattern=SORT_PATTERN)):
    repos = request.app.state.repos
    metric = sort in METRIC_SORTS
    limit = ru._WINDOW_LIMIT
    targets = _ref_scan_targets(repos.db, q=q or None, storage=storage or None, path=path or None,
                                order="desc" if metric else order, limit=limit + 1,
                                sort="last_scan" if metric else sort)
    truncated = len(targets) > limit
    targets = targets[:limit]
    recent = _ref_recent_scans_by_target(repos, per_target=2,
                                         pairs=[(t["storage_name"], t["target"]) for t in targets])
    latest_jobs = [js[0] for js in recent.values()]
    digests = _ref_digests(repos, ru._base(request), latest_jobs)
    prev_cached = repos.scan_digests.get_many([js[1]["job_id"] for js in recent.values() if len(js) > 1])
    infos = ru._storage_infos(repos, dict.fromkeys(t["storage_name"] for t in targets))
    rows = []
    for t in targets:
        js = recent.get((t["storage_name"], t["target"])) or []
        rows.append({**t,
                     "storage": infos.get(t["storage_name"]),
                     "latest": ru._point(js[0], digests.get(js[0]["job_id"])) if js else None,
                     "previous": ru._previous(js[1], prev_cached.get(js[1]["job_id"])) if len(js) > 1 else None})
    if metric:
        rows = _ref_sort_by_metric(rows, sort, order)
    body = {"generated_at": utc_now_iso(), "count": len(rows), "truncated": truncated, "rows": rows}
    return Response(content=json.dumps(body, separators=(",", ":")).encode(), media_type="application/json")


# ======================================================================================================================
# 픽스처
# ======================================================================================================================

@pytest.fixture(params=["sqlite", "postgresql"])
def eq_db(request, tmp_path):
    if request.param == "sqlite":
        db = Database.connect(f"sqlite:///{tmp_path}/eq.db")
        # 비교 수백 번 x 문장마다 fsync 면 수십 초다 -- 내구성이 무관한 일회용 DB 라 끈다(동작·계획은 같다).
        db.execute("PRAGMA synchronous = OFF")
        migrate(db)
        yield db
        return
    url = os.environ.get("DMS_TEST_PG_URL")
    if not url:
        pytest.skip("DMS_TEST_PG_URL 없음(일회용 PostgreSQL 에서만 돈다)")
    import psycopg
    schema = "dms_eq_" + uuid.uuid4().hex[:12]
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(f"CREATE SCHEMA {schema}")
    db = Database.connect(f"{url}{'&' if '?' in url else '?'}options=-csearch_path%3D{schema}")
    try:
        migrate(db)
        yield db
    finally:
        db._conn.close()
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute(f"DROP SCHEMA {schema} CASCADE")


def _report(*, total, files, hot, cold):
    """summary·time_histograms 의 키 순서가 정렬 순서가 **아닌** 리포트(dscan 의 실제 순서) -- 빈 캐시(리포트 투영)와 데운
    캐시(dump_json sort_keys)의 차이가 비교에 드러나게."""
    return {
        "directory": "/cephfs/dms/x", "generated_at_epoch": 1785805962,
        "summary": {"total_entries": files + 3, "total_files": files, "total_directories": 3, "total_symlinks": 0,
                    "total_other": 0, "scan_errors": 0},
        "file_size_histogram": [{"bucket": "[0,4096]", "lower_inclusive": 0, "upper_inclusive": 4096, "count": files}],
        "time_histograms": {
            "atime": [{"bucket": "[0d,7d]", "min_age_days": 0, "max_age_days": 7, "bytes": hot},
                      {"bucket": "[181d,365d]", "min_age_days": 181, "max_age_days": 365, "bytes": cold}],
            "mtime": [{"bucket": "[0d,1d]", "min_age_days": 0, "max_age_days": 1, "bytes": total}],
            "ctime": []},
        "broken_paths_total": 0, "broken_paths_limit": 100, "broken_paths": []}


class _Seeder:
    def __init__(self, repos, art):
        self.repos, self.art, self.n = repos, art, 0

    def scan(self, storage, target, at, *, report=None, raw=None, db_bytes=None, state=DataJobState.SUCCEEDED,
             op="scan"):
        self.n += 1
        repos = self.repos
        rid = repos.requests.create(operation=op, requester_id="alice", actor="alice",
                                    resource_key=f"k:{self.n}", payload={"storage": storage, "target": target},
                                    priority="mid")
        plan_id = repos.data_jobs.create_plan(rid, actor="planner")
        jid = repos.data_jobs.create_job(
            rid, plan_id, operation=op, priority="mid", storage_name=storage, target=target, options={},
            tool="dscan", worker_pool={"identity": {"username": "alice", "uid": os.getuid(), "gid": os.getgid()}},
            precondition={}, actor="planner")
        repos.data_jobs.set_job_state(jid, state, actor="stepper")
        if db_bytes is not None:
            repos.data_jobs.set_artifact(jid, artifact_uri="file:///x", result_summary={"bytes": db_bytes})
        repos.db.execute("UPDATE data_jobs SET updated_at = :t WHERE job_id = :j", {"t": at, "j": jid})
        if report is not None or raw is not None:
            d = self.art / jid / "execution"
            d.mkdir(parents=True)
            (d / "dscan-report.json").write_text(raw if raw is not None else json.dumps(report))
        return jid


def _seed(repos, art):
    """정렬·필터·묶음 경계·신뢰 경계의 모서리를 한 DB 에: 같은 타깃 이름이 여러 스토리지에(team), 같은 시각의 동률(지표·
    최근 스캔·job_id), 비 ASCII·LIKE 메타문자 경로, 리포트 부재(DB bytes 있음/없음)·깨짐, 0 값, 미등록(감사 있음/없음),
    storage_name NULL·빈 문자열(변조), 사용량 밖(실패 scan·sync·target NULL)."""
    s = _Seeder(repos, art)
    repos.storages.create(storage_name="s1", mount_path="/s1", managed_root="/s1/m", backend_type="cephfs", actor="t")
    repos.storages.create(storage_name="s2", mount_path="/s2", managed_root="/s2/m", backend_type="gpfs", actor="t")
    repos.storages.create(storage_name="old", mount_path="/old", managed_root="/old/m", backend_type="wekafs",
                          actor="t")
    repos.storages.delete("old", actor="t")
    r = _report
    s.scan("s1", "team", "2026-09-01T00:00:00Z", report=r(total=100, files=1, hot=1, cold=1), db_bytes=99)
    s.scan("s1", "team", "2026-09-05T00:00:00Z", report=r(total=300, files=3, hot=1, cold=3))
    s.scan("s1", "team", "2026-09-09T00:00:00Z", report=r(total=200, files=20, hot=5, cold=5))
    s.scan("s1", "team", "2026-09-10T00:00:00Z", report=r(total=1, files=1, hot=1, cold=1),
           state=DataJobState.FAILED)                                                  # 실패는 사용량 밖
    s.scan("s1", "team/sub", "2026-09-09T00:00:00Z", report=r(total=200, files=20, hot=5, cold=5))  # 동률
    s.scan("s1", "팀/데이터", "2026-09-03T00:00:00Z", report=r(total=50, files=5, hot=9, cold=1))
    s.scan("s1", "팀/데이터", "2026-09-04T00:00:00Z", report=r(total=60, files=6, hot=9, cold=1))
    s.scan("s1", "100%_x", "2026-08-01T00:00:00Z", report=r(total=900, files=2, hot=0, cold=9))
    s.scan("s1", "nobytes", "2026-09-07T00:00:00Z")                                     # 리포트 부재·DB 값 없음
    s.scan("s1", "dbbytes", "2026-09-06T00:00:00Z", db_bytes=777)                       # 리포트 부재·DB 값 있음
    s.scan("s1", "broken", "2026-09-08T00:00:00Z", raw="{bro", db_bytes=5)
    s.scan("s1", "zero", "2026-09-02T00:00:00Z", report=r(total=0, files=0, hot=0, cold=0))
    s.scan("s1", "tie-b", "2026-09-04T12:00:00Z", report=r(total=70, files=7, hot=1, cold=1))
    s.scan("s1", "tie-a", "2026-09-04T12:00:00Z", report=r(total=70, files=7, hot=1, cold=1))
    s.scan("s2", "team", "2026-09-06T00:00:00Z", report=r(total=4000, files=40, hot=1, cold=0))  # 같은 이름, 다른 곳
    s.scan("s2", "proj/a", "2026-09-02T00:00:00Z", report=r(total=10, files=1, hot=1, cold=0))
    s.scan("s2", "proj/a", "2026-09-02T00:00:00Z", report=r(total=11, files=2, hot=0, cold=1))  # 같은 시각 -- job_id
    s.scan("s2", "proj/b", "2026-08-20T00:00:00Z", report=r(total=12, files=3, hot=2, cold=2))
    s.scan("old", "legacy", "2026-07-01T00:00:00Z", report=r(total=5, files=5, hot=0, cold=5), db_bytes=4)
    s.scan("old", "legacy", "2026-07-02T00:00:00Z", report=r(total=6, files=6, hot=0, cold=6))
    s.scan("old", "team", "2026-07-03T00:00:00Z", db_bytes=33)
    s.scan("ghost", "x", "2026-06-01T00:00:00Z", report=r(total=8, files=8, hot=8, cold=0))
    orphan = s.scan("s1", "orphan", "2026-09-11T00:00:00Z", report=r(total=999, files=9, hot=9, cold=0))
    blank = s.scan("s1", "blank", "2026-09-11T00:00:00Z", report=r(total=998, files=9, hot=9, cold=0))
    repos.db.execute("UPDATE data_jobs SET storage_name = NULL WHERE job_id = :j", {"j": orphan})
    repos.db.execute("UPDATE data_jobs SET storage_name = '' WHERE job_id = :j", {"j": blank})
    s.scan("s1", "synced", "2026-09-12T00:00:00Z", op="sync")                          # 사용량 밖
    notarget = s.scan("s1", "nt", "2026-09-12T00:00:00Z", report=r(total=1, files=1, hot=1, cold=1))
    repos.db.execute("UPDATE data_jobs SET target = NULL WHERE job_id = :j", {"j": notarget})


@pytest.fixture
def eq_client(eq_db, tmp_path):
    from dms.api.app import create_app
    art = tmp_path / "artifacts"
    art.mkdir()
    settings = Settings(database_url="unused", shared_token="tok-shared", admin_token="tok-admin",
                        session_secret="sess-secret", artifact_base_uri=f"file://{art}")
    client = TestClient(create_app(settings, eq_db))
    client.app.router.add_api_route("/ref/scan-targets", ref_scan_targets, methods=["GET"])
    client.app.router.add_api_route("/ref/export", ref_export_usage, methods=["GET"])
    client.app.state.repos.accounts.create("admin", "pw", "admin", actor="t")
    assert client.post("/api/auth/login", json={"username": "admin", "password": "pw"}).status_code == 200
    _seed(client.app.state.repos, art)
    return client


def _clear_cache(client):
    client.app.state.repos.db.execute("DELETE FROM scan_report_digests")


def _canon(body: bytes):
    return json.dumps(json.loads(body), sort_keys=True)


def _expected_export(ref: bytes, new: bytes) -> bytes:
    """옛 본문 {generated_at, count, truncated, rows} 의 rows 바이트를 그대로 잘라 새 순서 {generated_at, truncated, rows,
    count} 로 다시 맞춘다(generated_at 은 새 본문의 값 -- 시각이다)."""
    old = json.loads(ref)
    cut = ref.index(b',"rows":') + len(b',"rows":')
    rows = ref[cut:-1]
    assert ref.endswith(b"]}") and json.loads(rows) == old["rows"]
    gen = json.loads(new)["generated_at"]
    return (b'{"generated_at":' + json.dumps(gen).encode() + b',"truncated":' + json.dumps(old["truncated"]).encode()
            + b',"rows":' + rows + b',"count":' + str(old["count"]).encode() + b"}")


# storage: None = 옛 탭의 「전체」(스토리지가 섞인 묶음), s1 = 등록·모서리 대부분, s2 = 같은 시각 job_id 동률, old·ghost =
# 미등록(감사 있음·없음), nope = 빈 결과.
LIST_CASES = ([{"storage": s, "sort": so, "order": o}
               for s in (None, "s1", "s2", "old", "ghost", "nope") for so in SORTS for o in ("desc", "asc")]
              + [{"path": "team", "sort": so} for so in ("scans", "bytes")]
              + [{"storage": "s1", "path": "데이터", "sort": "bytes"}, {"storage": "s1", "path": "%", "sort": "hot"},
                 {"q": "s2", "sort": "files", "order": "asc"}, {"q": "team", "storage": "s1", "sort": "scans"}])
EXPORT_CASES = ([{"storage": s, "sort": so, "order": o}
                 for s in (None, "s1", "s2", "old", "ghost") for so in SORTS for o in ("desc", "asc")]
                + [{"path": "team", "sort": "bytes"}, {"q": "s2", "sort": "hot", "order": "asc"},
                   {"storage": "s1", "path": "데이터", "sort": "last_scan"}, {"storage": "nope", "sort": "files"}])


def _params(case):
    return {k: v for k, v in case.items() if v is not None}


def _get(client, url, case):
    r = client.get(url, params=_params(case))
    assert r.status_code == 200, (url, case, r.text)
    return r.content


@pytest.mark.parametrize("window,chunk,limit", [(10000, None, 100), (3, 2, 2)],
                         ids=["window10000", "window3-chunk2-limit2"])
def test_list_matches_the_reference_byte_for_byte(eq_client, monkeypatch, window, chunk, limit):
    # 창 3·묶음 2·limit 2: 창 밖의 큰 타깃(100%_x)이 빠지고, 묶음 경계가 여러 번 지나가고, limit 이 정렬 뒤에 자른다.
    monkeypatch.setattr(ru, "_WINDOW_LIMIT", window)
    if chunk is not None:
        monkeypatch.setattr(ru, "_CHUNK", chunk)
        monkeypatch.setattr(data_jobs_mod, "_IN_CHUNK", chunk)
    client = eq_client
    for case in LIST_CASES:
        case = {"limit": limit, **case}
        metric = case["sort"] in METRIC_SORTS
        # 빈 캐시(첫 호출)
        _clear_cache(client)
        ref_cold = _get(client, "/ref/scan-targets", case)
        _clear_cache(client)
        new_cold = _get(client, "/api/admin/usage/scan-targets", case)
        if metric:
            assert _canon(new_cold) == _canon(ref_cold), case
            assert ([(r["storage_name"], r["target"]) for r in json.loads(new_cold)]
                    == [(r["storage_name"], r["target"]) for r in json.loads(ref_cold)]), case
        else:
            assert new_cold == ref_cold, case
        # 데운 캐시(그 뒤 호출) -- 참조를 한 번 더 돌려 캐시를 같은 상태로 채운 뒤
        ref_warm = _get(client, "/ref/scan-targets", case)
        new_warm = _get(client, "/api/admin/usage/scan-targets", case)
        assert new_warm == ref_warm, case
        if metric:
            assert new_cold == ref_warm, case          # 지표 정렬의 첫 호출은 데운 캐시 형태다(모듈 docstring)


@pytest.mark.parametrize("window,chunk", [(10000, None), (3, 2)], ids=["window10000", "window3-chunk2"])
def test_export_matches_the_reference_byte_for_byte(eq_client, monkeypatch, window, chunk):
    monkeypatch.setattr(ru, "_WINDOW_LIMIT", window)
    if chunk is not None:
        monkeypatch.setattr(ru, "_CHUNK", chunk)
        monkeypatch.setattr(data_jobs_mod, "_IN_CHUNK", chunk)
    client = eq_client
    seen_truncated = set()
    for case in EXPORT_CASES:
        metric = case["sort"] in METRIC_SORTS
        _clear_cache(client)
        ref_cold = _get(client, "/ref/export", case)
        _clear_cache(client)
        new_cold = _get(client, "/api/admin/usage/export", case)
        if metric:
            assert _canon(_expected_export(ref_cold, new_cold)) == _canon(new_cold), case
        else:
            assert new_cold == _expected_export(ref_cold, new_cold), case
        ref_warm = _get(client, "/ref/export", case)
        new_warm = _get(client, "/api/admin/usage/export", case)
        assert new_warm == _expected_export(ref_warm, new_warm), case
        if metric:
            assert new_cold == _expected_export(ref_warm, new_cold), case
        seen_truncated.add(json.loads(new_warm)["truncated"])
    # 창 3 에서는 잘림이, 1만에서는 안 잘림이 실제로 비교됐다(그물이 헛돌지 않게)
    assert seen_truncated == ({True, False} if window == 3 else {False})


def test_reference_fixture_exercises_the_edges(eq_client):
    # 그물이 헛돌지 않게: 픽스처가 노린 모서리가 실제 응답에 있다.
    client = eq_client
    rows = json.loads(_get(client, "/api/admin/usage/scan-targets", {"sort": "bytes", "limit": 100}))
    by = {(r["storage_name"], r["target"]): r for r in rows}
    assert by[(None, "orphan")]["latest"] is None                       # NULL 스토리지 = latest 없음(옛 판정)
    assert by[("", "blank")]["latest"]["total_bytes"] == 998            # 빈 이름은 이름이다
    assert by[("s1", "team")]["latest"]["total_bytes"] == 200 and by[("s2", "team")]["latest"]["total_bytes"] == 4000
    assert by[("s1", "dbbytes")]["latest"]["total_bytes"] == 777
    assert by[("s1", "nobytes")]["latest"]["total_bytes"] is None
    assert by[("s1", "zero")]["latest"]["total_bytes"] == 0
    assert ("s1", "nt") not in by and ("s1", "synced") not in by
    order = [(r["storage_name"], r["target"]) for r in rows]
    assert order.index(("s1", "tie-a")) + 1 == order.index(("s1", "tie-b"))     # 동률은 이름순
    assert order[-2:] == [(None, "orphan"), ("s1", "nobytes")]                  # 모름은 맨 뒤(최근 스캔 늦은 것부터)
    assert by[("s2", "proj/a")]["scan_count"] == 2
    truncated = json.loads(_get(client, "/api/admin/usage/export", {"storage": "s1"}))
    assert truncated["truncated"] is False and truncated["count"] == 10


# ======================================================================================================================
# 두 방언에서 도는 새 SQL(put_many 의 VALUES·ON CONFLICT, 탭 요약 GROUP BY)
# ======================================================================================================================

def _bulk_scan_jobs(db, n, *, storage="s1"):
    """성공 scan 잡 n 개를 문장 하나씩 한 트랜잭션에(요청·plan 없이 -- 사용량 질의는 data_jobs 만 본다)."""
    from dms.db import dump_json
    wp = dump_json({"identity": {"username": "alice", "uid": os.getuid(), "gid": os.getgid()}})
    ids = []
    with db.transaction():
        for i in range(n):
            jid = uuid.uuid4().hex
            at = f"2026-09-01T{i // 3600:02d}:{i // 60 % 60:02d}:{i % 60:02d}Z"
            db.execute("""INSERT INTO data_jobs (job_id, request_id, operation, tool, storage_name, target, options,
                                                 priority, state, worker_pool, created_at, updated_at)
                          VALUES (:j, :r, 'scan', 'dscan', :sn, :t, '{}', 'mid', 'Succeeded', :wp, :at, :at)""",
                       {"j": jid, "r": uuid.uuid4().hex, "sn": storage, "t": f"t{i:05d}", "wp": wp, "at": at})
            ids.append(jid)
    return ids


def test_put_many_writes_in_chunks_only_for_existing_jobs_and_keeps_the_first_value(eq_db):
    from dms.repositories import Repositories
    repos = Repositories(eq_db)
    ids = _bulk_scan_jobs(eq_db, 1200)
    repos.scan_digests.put(ids[0], {"v": "first"})
    statements = []
    real = eq_db.execute

    def spy(sql, params=None):
        if "INSERT INTO scan_report_digests" in sql:
            statements.append(sql.count("(:j"))
        return real(sql, params)
    eq_db.execute = spy
    repos.scan_digests.put_many({**{j: {"v": j} for j in ids}, "0" * 32: {"v": "no such job"}})
    repos.scan_digests.put_many({})
    eq_db.execute = real
    assert statements == [500, 500, 201]                       # 묶음 500 -- 한 문장에 그 이상 싣지 않는다
    got = repos.scan_digests.get_many(ids + ["0" * 32])
    assert len(got) == 1200 and "0" * 32 not in got             # 잡 행이 없으면 넣지 않는다(삭제 경합 가드)
    assert got[ids[0]] == {"v": "first"} and got[ids[1]] == {"v": ids[1]}   # 먼저 쓴 값이 남는다


def test_storages_summary_on_both_dialects(eq_client):
    body = eq_client.get("/api/admin/usage/storages").json()
    assert body["window_limit"] == 10000
    rows = [(r["storage_name"], r["registered"], r["source"], r["backend_type"], r["mount_path"], r["target_count"],
             r["succeeded_scan_count"], r["last_scan_at"]) for r in body["storages"]]
    assert rows == [
        # 등록 이름순(실패 scan·sync·target NULL·스토리지 NULL 은 세지 않는다)
        ("s1", True, "registered", "cephfs", "/s1", 10, 13, "2026-09-09T00:00:00Z"),
        ("s2", True, "registered", "gpfs", "/s2", 3, 4, "2026-09-06T00:00:00Z"),
        # 미등록 이름순 -- 빈 이름(변조 행)도 이름이다, 감사 기록이 있으면 마지막 설정, 없으면 모름
        ("", False, None, None, None, 1, 1, "2026-09-11T00:00:00Z"),
        ("ghost", False, None, None, None, 1, 1, "2026-06-01T00:00:00Z"),
        ("old", False, "last_known", "wekafs", "/old", 2, 3, "2026-07-03T00:00:00Z"),
    ]
