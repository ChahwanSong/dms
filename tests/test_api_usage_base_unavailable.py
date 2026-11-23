"""2026-11-23 사용량 집계의 「base 접근 불가」 처리(routes_usage + api/base_probe.py).

- 첫 읽기 실패에서 base 수준 판정을 한 번 묻고, 접근 불가면 남은 리포트를 열지 않고 멈춘다(멈춘·빠진 마운트에서 수백 건을
  하나씩 실패시키지 않는다).
- 응답은 원인을 말한다(unreadable_reason) -- 정상 경로의 바이트는 그대로(tests/test_usage_equivalence.py).
- 일시 장애를 영구 「모름」으로 굳히지 않는다: scan_report_digests 에 실패를 넣지 않고, storage_totals 는 실패 기억(TTL)에
  넣지 않고 pending 으로 센다.
"""
import json
import os

from fastapi.testclient import TestClient

from dms.api import routes_usage
from dms.api.app import create_app
from dms.config import Settings
from dms.db import Database
from dms.domain import DataJobState
from dms.migrations import migrate


def _report(total=100):
    return {"generated_at_epoch": 1785805962,
            "summary": {"total_entries": 3, "total_files": 2, "total_directories": 1, "total_symlinks": 0,
                        "total_other": 0, "scan_errors": 0},
            "file_size_histogram": [],
            "time_histograms": {"atime": [{"bucket": "[0d,1d]", "min_age_days": 0, "max_age_days": 1,
                                           "bytes": total}], "mtime": [], "ctime": []}}


def _client(tmp_path, base):
    db = Database.connect(f"sqlite:///{tmp_path}/t.db")
    migrate(db)
    settings = Settings(database_url="unused", shared_token="tok-shared", admin_token="tok-admin",
                        session_secret="sess-secret", artifact_base_uri=f"file://{base}")
    client = TestClient(create_app(settings, db))
    client.app.state.repos.accounts.create("admin", "pw", "admin", actor="t")
    client.post("/api/auth/login", json={"username": "admin", "password": "pw"})
    return client


def _scan(client, base, target, *, day="2026-09-01", report=True):
    repos = client.app.state.repos
    rid = repos.requests.create(operation="scan", requester_id="alice", actor="alice",
                                resource_key=f"k:{target}:{os.urandom(3).hex()}",
                                payload={"storage": "s1", "target": target}, priority="mid")
    jid = repos.data_jobs.create_job(
        rid, repos.data_jobs.create_plan(rid, actor="p"), operation="scan", priority="mid", storage_name="s1",
        target=target, options={}, tool="dscan",
        worker_pool={"identity": {"username": "alice", "uid": os.getuid(), "gid": os.getgid()}},
        precondition={}, actor="p")
    repos.data_jobs.set_job_state(jid, DataJobState.SUCCEEDED, actor="stepper")
    repos.requests.finalize_from_job(rid, DataJobState.SUCCEEDED, actor="stepper")
    repos.db.execute("UPDATE data_jobs SET updated_at = :u WHERE job_id = :j", {"u": f"{day}T00:00:00Z", "j": jid})
    if report:
        d = base / jid / "execution"
        d.mkdir(parents=True)
        (d / "dscan-report.json").write_text(json.dumps(_report()))
    return jid


def _count_reads(monkeypatch):
    calls = []
    real = routes_usage._read_digest

    def spy(base, job):
        calls.append(job["job_id"])
        return real(base, job)

    monkeypatch.setattr(routes_usage, "_read_digest", spy)
    return calls


def _unmount(base):
    base.rename(base.parent / (base.name + "-gone"))


def _remount(base):
    (base.parent / (base.name + "-gone")).rename(base)


def test_scan_history_names_the_cause_and_stops_after_the_first_failure(tmp_path, monkeypatch):
    base = tmp_path / "artifacts"
    client = _client(tmp_path, base)
    for day in ("2026-09-01", "2026-09-02", "2026-09-03"):
        _scan(client, base, "team", day=day)
    _unmount(base)
    reads = _count_reads(monkeypatch)
    body = client.get("/api/admin/usage/scan-history", params={"storage": "s1", "target": "team"}).json()
    assert (body["skipped_unreadable"], body["points"], body["unreadable_reason"]) == (
        3, [], "artifact_base_unavailable")
    assert len(reads) == 1                       # 남은 둘은 열지 않았다


def test_scan_history_genuine_missing_report_has_no_cause(tmp_path):
    base = tmp_path / "artifacts"
    client = _client(tmp_path, base)
    _scan(client, base, "team", day="2026-09-01")
    _scan(client, base, "team", day="2026-09-02", report=False)       # base 는 보이고 그 파일만 없다
    body = client.get("/api/admin/usage/scan-history", params={"storage": "s1", "target": "team"}).json()
    assert (body["skipped_unreadable"], len(body["points"]), body["unreadable_reason"]) == (1, 1, None)


def test_failures_never_reach_the_digest_cache(tmp_path, monkeypatch):
    base = tmp_path / "artifacts"
    client = _client(tmp_path, base)
    _scan(client, base, "team")
    _unmount(base)
    puts = []
    repo = client.app.state.repos.scan_digests
    real = repo.put_many
    monkeypatch.setattr(repo, "put_many", lambda d: puts.append(dict(d)) or real(d))
    client.get("/api/admin/usage/scan-history", params={"storage": "s1", "target": "team"})
    client.get("/api/admin/usage/scan-targets")
    client.get("/api/admin/usage/storage-totals", params={"storage": "s1"})
    assert puts == []
    assert client.app.state.repos.db.query_one("SELECT COUNT(*) AS n FROM scan_report_digests")["n"] == 0


def test_storage_totals_counts_pending_and_does_not_remember_failures(tmp_path):
    base = tmp_path / "artifacts"
    client = _client(tmp_path, base)
    jids = [_scan(client, base, t) for t in ("a", "b", "c")]
    _unmount(base)
    t = client.get("/api/admin/usage/storage-totals", params={"storage": "s1"}).json()
    # 처음 실패한 잡까지 pending -- 「이 리포트를 못 읽음」(unreadable)이 아니다
    assert (t["unreadable_count"], t["pending_count"], t["progressed"], t["unreadable_reason"]) == (
        0, 3, False, "artifact_base_unavailable")
    recent, expired = client.app.state.report_read_failures.split(jids)
    assert recent == set() and expired == set()  # 실패 기억(TTL 10분)에 넣지 않았다 -- 복구 뒤 바로 읽는다
    _remount(base)
    t = client.get("/api/admin/usage/storage-totals", params={"storage": "s1"}).json()
    assert (t["unreadable_count"], t["pending_count"], t["progressed"], t["unreadable_reason"]) == (
        0, 0, True, None)
    assert t["total_bytes"]["known"] == 3


def test_scan_targets_serves_cache_hits_and_marks_only_unread_rows(tmp_path):
    base = tmp_path / "artifacts"
    client = _client(tmp_path, base)
    _scan(client, base, "cached", day="2026-09-01")
    assert client.get("/api/admin/usage/scan-targets").status_code == 200    # 캐시를 데운다
    _scan(client, base, "fresh", day="2026-09-02")
    _unmount(base)
    rows = {r["target"]: r for r in client.get("/api/admin/usage/scan-targets").json()}
    assert rows["cached"]["latest"]["report_readable"] is True               # 캐시 적중은 base 와 무관
    assert "unreadable_reason" not in rows["cached"]["latest"]
    assert rows["fresh"]["latest"]["report_readable"] is False
    assert rows["fresh"]["latest"]["unreadable_reason"] == "artifact_base_unavailable"
    paged = client.get("/api/admin/usage/scan-targets", params={"offset": 0}).json()
    assert paged["unreadable_reason"] == "artifact_base_unavailable"
    _remount(base)
    paged = client.get("/api/admin/usage/scan-targets", params={"offset": 0}).json()
    assert paged["unreadable_reason"] is None
    assert all("unreadable_reason" not in r["latest"] for r in paged["rows"])


def test_metric_sort_skips_remaining_chunks_once_down(tmp_path, monkeypatch):
    base = tmp_path / "artifacts"
    client = _client(tmp_path, base)
    for i in range(4):
        _scan(client, base, f"t{i}", day=f"2026-09-0{i + 1}")
    _unmount(base)
    monkeypatch.setattr(routes_usage, "_CHUNK", 1)
    lookups = []
    repo = client.app.state.repos.data_jobs
    real = repo.jobs_by_ids
    monkeypatch.setattr(repo, "jobs_by_ids", lambda ids: lookups.append(list(ids)) or real(ids))
    reads = _count_reads(monkeypatch)
    rows = client.get("/api/admin/usage/scan-targets", params={"sort": "bytes"}).json()
    assert len(lookups) == 1 and len(reads) == 1     # 첫 묶음에서 멈추고 남은 묶음은 잡 행 조회·열기를 건너뛴다
    assert all(r["latest"]["unreadable_reason"] == "artifact_base_unavailable" for r in rows)


def test_export_marks_rows_and_keeps_the_tail(tmp_path):
    base = tmp_path / "artifacts"
    client = _client(tmp_path, base)
    _scan(client, base, "team")
    _unmount(base)
    raw = client.get("/api/admin/usage/export").content
    assert raw.endswith(b'],"count":1}')
    body = json.loads(raw)
    assert body["rows"][0]["latest"]["unreadable_reason"] == "artifact_base_unavailable"
    assert set(body) == {"generated_at", "truncated", "rows", "count"}


def test_hung_mount_with_everything_cached_reports_no_reason(tmp_path, monkeypatch):
    # 2026-11-23 리뷰: 열 리포트가 없으면(다 캐시) 판정도 하지 않는다 -- 멈춘 마운트여도 최상위 unreadable_reason 이 서면
    # 포탈이 거짓 「일부 대상을 읽지 못했습니다」를 띄우고 복구 감시를 돈다.
    import threading
    base = tmp_path / "artifacts"
    client = _client(tmp_path, base)
    _scan(client, base, "team")
    params = {"storage": "s1", "target": "team"}
    assert client.get("/api/admin/usage/scan-history", params=params).json()["points"]     # 정상일 때 캐시를 채운다
    runner = client.app.state.base_check_runner
    release = threading.Event()
    runner.run(("api", str(base)), lambda: release.wait(10), timeout=0.05)
    original = runner.stuck_since
    monkeypatch.setattr(runner, "stuck_since", lambda key, timeout=0.05: original(key, timeout=0.05))
    try:
        body = client.get("/api/admin/usage/scan-history", params=params).json()
        assert body["unreadable_reason"] is None and body["skipped_unreadable"] == 0 and body["points"]
        page = client.get("/api/admin/usage/scan-targets", params={"storage": "s1", "offset": 0}).json()
        assert page["unreadable_reason"] is None
    finally:
        release.set()


def test_hung_mount_opens_nothing(tmp_path, monkeypatch):
    # 같은 키의 점검이 마감을 넘겨 멈춰 있으면 집계 라우트는 리포트를 하나도 열지 않는다(down 취급).
    import threading
    base = tmp_path / "artifacts"
    client = _client(tmp_path, base)
    _scan(client, base, "team")
    runner = client.app.state.base_check_runner
    release = threading.Event()
    runner.run(("api", str(base)), lambda: release.wait(10), timeout=0.05)
    original = runner.stuck_since
    monkeypatch.setattr(runner, "stuck_since", lambda key, timeout=0.05: original(key, timeout=0.05))
    reads = _count_reads(monkeypatch)
    try:
        body = client.get("/api/admin/usage/scan-history", params={"storage": "s1", "target": "team"}).json()
        assert body["unreadable_reason"] == "artifact_base_unavailable" and reads == []
    finally:
        release.set()
