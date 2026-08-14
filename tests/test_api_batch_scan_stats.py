"""GET /api/admin/batches/{id}/scan-stats — scan 배치의 성공 자식 dscan 리포트를
합산한 hot/cold 집계. 노출은 scan_path_stats 와 같은 **모양 투영**(숫자·구간
라벨만)을 공유한다 — 리포트 안의 경로가 어떤 자리로도 새면 안 된다. 정직 카운트:
aggregated(합산한 리포트 수)·skipped(truncated/부재/파싱 실패/캡 초과)."""
import json

from dms.config import Settings
from dms.db import Database
from dms.domain import DataJobState
from dms.migrations import migrate
from fastapi.testclient import TestClient


def _report(*, files=7, size_count=7, atime_bytes=50, broken=3):
    return {
        "directory": "/cephfs/dms/team",
        "generated_at_epoch": 1785805962,
        "summary": {"total_entries": 10, "total_files": files,
                    "total_directories": 3, "total_symlinks": 0,
                    "total_other": 0, "scan_errors": 0},
        "file_size_histogram": [{"bucket": "[0,4096]", "lower_inclusive": 0,
                                 "upper_inclusive": 4096, "count": size_count}],
        "time_histograms": {"atime": [{"bucket": "[0d,1d]", "min_age_days": 0,
                                       "max_age_days": 1, "bytes": atime_bytes}],
                            "mtime": [], "ctime": []},
        "broken_paths_total": broken,
        "broken_paths_limit": 100,
        "broken_paths": [{"path": "/cephfs/dms/team/secret.txt",
                          "reasons": ["missing"]}],
    }


def _client(tmp_path, artifact_base):
    from dms.api.app import create_app
    db = Database.connect(f"sqlite:///{tmp_path}/test.db")
    migrate(db)
    settings = Settings(database_url="unused", shared_token="tok-shared",
                        admin_token="tok-admin", session_secret="sess-secret",
                        artifact_base_uri=f"file://{artifact_base}")
    return TestClient(create_app(settings, db))


def _admin(client):
    client.app.state.repos.accounts.create("admin", "pw", "admin", actor="t")
    client.post("/api/auth/login", json={"username": "admin", "password": "pw"})


def _scan_batch(client, n=1):
    items = [{"storage": "s1", "target": f"t{i}"} for i in range(n)]
    r = client.post("/api/admin/batches", json={"operation": "scan",
        "max_concurrency": 2, "options": {}, "note": None, "items": items})
    assert r.status_code == 202, r.text
    return r.json()["batch_id"]


def _succeed_child(client, bid, seq, *, art_base=None, report=None,
                   raw_report=None, write_report=True):
    """item(seq)을 성공 종단 자식(요청+잡)으로 손수 materialize 하고, 성공 잡의
    execution/dscan-report.json 을 써 둔다(test_api_scan_path_stats 관례)."""
    repos = client.app.state.repos
    target = f"t{seq}"
    rid = repos.requests.create(operation="scan", requester_id="admin",
        actor="admin", resource_key=f"data.scan:s1:{target}:admin",
        payload={"storage": "s1", "target": target}, priority="mid", batch_id=bid)
    repos.batches.set_item_materialized(bid, seq, rid)
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan_id, operation="scan",
        priority="mid", storage_name="s1", target=target, options={},
        tool="dscan", worker_pool={}, precondition={}, actor="planner")
    repos.data_jobs.set_job_state(jid, DataJobState.SUCCEEDED, actor="stepper")
    repos.requests.finalize_from_job(rid, DataJobState.SUCCEEDED, actor="stepper")
    repos.batches.set_item_status(bid, seq, "Succeeded")
    if write_report:
        d = art_base / jid / "execution"
        d.mkdir(parents=True)
        body = raw_report if raw_report is not None else json.dumps(
            _report() if report is None else report)
        (d / "dscan-report.json").write_text(body)
    return jid


def test_aggregates_two_reports(tmp_path):
    art_base = tmp_path / "artifacts"
    client = _client(tmp_path, art_base)
    _admin(client)
    bid = _scan_batch(client, n=2)
    _succeed_child(client, bid, 0, art_base=art_base,
                   report=_report(files=7, size_count=7, atime_bytes=50, broken=3))
    _succeed_child(client, bid, 1, art_base=art_base,
                   report=_report(files=5, size_count=4, atime_bytes=20, broken=1))
    r = client.get(f"/api/admin/batches/{bid}/scan-stats")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["aggregated"] == 2 and body["skipped"] == 0
    assert body["summary"]["total_files"] == 12
    assert body["summary"]["total_entries"] == 20
    assert body["file_size_histogram"] == [
        {"bucket": "[0,4096]", "lower_inclusive": 0, "upper_inclusive": 4096,
         "count": 11}]
    assert body["time_histograms"]["atime"] == [
        {"bucket": "[0d,1d]", "min_age_days": 0, "max_age_days": 1, "bytes": 70}]
    assert body["broken_paths_total"] == 4


def test_sync_batch_rejected_422(tmp_path):
    art_base = tmp_path / "artifacts"
    client = _client(tmp_path, art_base)
    _admin(client)
    r = client.post("/api/admin/batches", json={"operation": "sync",
        "max_concurrency": 1, "options": {}, "note": None,
        "items": [{"source_storage": "s1", "source": "a",
                   "destination_storage": "s2", "destination": "b"}]})
    bid = r.json()["batch_id"]
    r = client.get(f"/api/admin/batches/{bid}/scan-stats")
    # sync 배치엔 dscan 리포트가 없다 — 빈 집계로 답하면 "스캔했는데 0"과 구분이
    # 안 되므로 422 가 정직하다.
    assert r.status_code == 422 and r.json()["detail"] == "invalid_batch_operation"


def test_missing_batch_404(tmp_path):
    client = _client(tmp_path, tmp_path / "artifacts")
    _admin(client)
    r = client.get("/api/admin/batches/nope/scan-stats")
    assert r.status_code == 404 and r.json()["detail"] == "batch_not_found"


def test_missing_and_broken_reports_counted_as_skipped(tmp_path):
    art_base = tmp_path / "artifacts"
    client = _client(tmp_path, art_base)
    _admin(client)
    bid = _scan_batch(client, n=3)
    _succeed_child(client, bid, 0, art_base=art_base)          # 정상 리포트
    _succeed_child(client, bid, 1, art_base=art_base, write_report=False)  # 부재
    _succeed_child(client, bid, 2, art_base=art_base, raw_report="{ not json")
    r = client.get(f"/api/admin/batches/{bid}/scan-stats")
    assert r.status_code == 200
    body = r.json()
    # 못 읽은 리포트는 조용히 사라지지 않는다 — skipped 로 드러난다
    assert body["aggregated"] == 1 and body["skipped"] == 2
    assert body["summary"]["total_files"] == 7


def test_truncated_report_is_skipped_not_503(tmp_path):
    """읽기 상한(256 KiB) 초과 리포트는 꼬리만 와서 JSON 이 깨진다. 단일 리포트를
    섬기는 scan_path_stats 는 503 이 정직하지만, 여기는 합산이라 "이 리포트는
    합산에서 빠졌다"를 skipped 로 세는 쪽이 정직하다(나머지 집계는 유효)."""
    art_base = tmp_path / "artifacts"
    client = _client(tmp_path, art_base)
    _admin(client)
    bid = _scan_batch(client, n=2)
    _succeed_child(client, bid, 0, art_base=art_base)
    big = _report()
    big["broken_paths"] = [{"path": f"/cephfs/dms/team/f{i}", "reasons": ["missing"]}
                           for i in range(6000)]
    raw = json.dumps(big)
    assert len(raw.encode()) > 256 * 1024
    _succeed_child(client, bid, 1, art_base=art_base, raw_report=raw)
    r = client.get(f"/api/admin/batches/{bid}/scan-stats")
    assert r.status_code == 200
    assert r.json()["aggregated"] == 1 and r.json()["skipped"] == 1


def test_shape_projection_never_leaks_paths(tmp_path):
    art_base = tmp_path / "artifacts"
    client = _client(tmp_path, art_base)
    _admin(client)
    bid = _scan_batch(client, n=1)
    secret = "/cephfs/dms/team/secret.txt"
    planted = _report()
    planted["summary"]["largest_file"] = secret
    planted["file_size_histogram"][0]["example_path"] = secret
    planted["file_size_histogram"].append({"bucket": secret, "count": 1})
    planted["time_histograms"]["atime"][0]["oldest_path"] = secret
    planted["time_histograms"][secret] = [{"bucket": "[0d,1d]", "bytes": 1}]
    _succeed_child(client, bid, 0, art_base=art_base, report=planted)
    r = client.get(f"/api/admin/batches/{bid}/scan-stats")
    assert r.status_code == 200
    raw = r.text
    assert "secret.txt" not in raw
    assert "/cephfs" not in raw
    assert "largest_file" not in raw
    body = r.json()
    assert body["summary"]["total_files"] == 7      # 수치는 남는다
    assert body["broken_paths_total"] == 3


def test_report_cap_puts_overflow_into_skipped(tmp_path, monkeypatch):
    """상한(200)을 넘는 후보는 읽지 않고 skipped 로 센다 — 조용한 절단 금지.
    실 상한으로 200개 잡을 만들면 느리므로 캡 상수를 1 로 내려 검증한다."""
    from dms.api import routes_batches as mod
    art_base = tmp_path / "artifacts"
    client = _client(tmp_path, art_base)
    _admin(client)
    bid = _scan_batch(client, n=2)
    _succeed_child(client, bid, 0, art_base=art_base)
    _succeed_child(client, bid, 1, art_base=art_base)
    monkeypatch.setattr(mod, "_SCAN_STATS_REPORT_CAP", 1)
    r = client.get(f"/api/admin/batches/{bid}/scan-stats")
    assert r.status_code == 200
    assert r.json()["aggregated"] == 1 and r.json()["skipped"] == 1


def test_mixed_bucket_order_sums_by_label_first_report_wins_order(tmp_path):
    art_base = tmp_path / "artifacts"
    client = _client(tmp_path, art_base)
    _admin(client)
    bid = _scan_batch(client, n=2)
    a = _report()
    a["file_size_histogram"] = [
        {"bucket": "[0,4096]", "count": 1}, {"bucket": "(4096,65536]", "count": 2}]
    b = _report()
    b["file_size_histogram"] = [                     # 순서가 다르고 새 라벨도 있다
        {"bucket": "(4096,65536]", "count": 10}, {"bucket": "[0,4096]", "count": 20},
        {"bucket": "(65536,1048576]", "count": 30}]
    _succeed_child(client, bid, 0, art_base=art_base, report=a)
    _succeed_child(client, bid, 1, art_base=art_base, report=b)
    r = client.get(f"/api/admin/batches/{bid}/scan-stats")
    assert r.status_code == 200
    hist = r.json()["file_size_histogram"]
    # 첫 리포트의 버킷 순서 기준 + 라벨 매칭 합산, 새 라벨은 뒤에 붙는다
    assert [(h["bucket"], h["count"]) for h in hist] == [
        ("[0,4096]", 21), ("(4096,65536]", 12), ("(65536,1048576]", 30)]


def test_old_reports_without_broken_total_keep_null(tmp_path):
    art_base = tmp_path / "artifacts"
    client = _client(tmp_path, art_base)
    _admin(client)
    bid = _scan_batch(client, n=1)
    old = _report()
    del old["broken_paths_total"], old["broken_paths_limit"]
    _succeed_child(client, bid, 0, art_base=art_base, report=old)
    r = client.get(f"/api/admin/batches/{bid}/scan-stats")
    assert r.status_code == 200
    # 전 리포트가 구형(총계 미기록)이면 합계도 None — 0(파손 없음)과 다르다(null≠0)
    assert r.json()["broken_paths_total"] is None


def test_requires_admin(tmp_path):
    client = _client(tmp_path, tmp_path / "artifacts")
    r = client.get("/api/admin/batches/x/scan-stats")
    assert r.status_code == 401
