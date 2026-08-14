"""GET /api/admin/requests/{request_id}/scan-stats — 그 요청의 성공 scan 잡
리포트 1건을 모양 투영(숫자·구간 라벨만, scan_path_stats 와 한 벌)해 서빙한다.
배치 합산(/api/admin/batches/{id}/scan-stats)은 제거됐다 — 사용자 결정: 온도는
배치 전체가 아니라 항목별 리포트로 본다. 단일 리포트 계약이라 truncated 는 503
scan_report_too_large(scan_path_stats 선례), 리포트가 애초에 없는 경우(비 scan·
성공 잡 없음·파일 부재·파싱 불가)는 전부 404 no_scan_report 다."""
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


def _request(client, *, operation="scan", target="team"):
    repos = client.app.state.repos
    payload = ({"storage": "s1", "target": target} if operation != "sync" else
               {"source_storage": "s1", "source": "a",
                "destination_storage": "s2", "destination": "b"})
    return repos.requests.create(operation=operation, requester_id="admin",
        actor="admin", resource_key=f"data.{operation}:s1:{target}:admin",
        payload=payload, priority="mid")


def _succeed_job(client, rid, *, operation="scan", target="team",
                 art_base=None, report=None, raw_report=None, write_report=True):
    """요청의 성공 종단 잡을 손수 materialize 하고 execution/dscan-report.json 을
    써 둔다(test_api_scan_path_stats 관례)."""
    repos = client.app.state.repos
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan_id, operation=operation,
        priority="mid", storage_name="s1", target=target, options={},
        tool="dscan", worker_pool={}, precondition={}, actor="planner")
    repos.data_jobs.set_job_state(jid, DataJobState.SUCCEEDED, actor="stepper")
    repos.requests.finalize_from_job(rid, DataJobState.SUCCEEDED, actor="stepper")
    if write_report:
        d = art_base / jid / "execution"
        d.mkdir(parents=True)
        body = raw_report if raw_report is not None else json.dumps(
            _report() if report is None else report)
        (d / "dscan-report.json").write_text(body)
    return jid


def test_serves_single_report_projection(tmp_path):
    art_base = tmp_path / "artifacts"
    client = _client(tmp_path, art_base)
    _admin(client)
    rid = _request(client)
    _succeed_job(client, rid, art_base=art_base)
    r = client.get(f"/api/admin/requests/{rid}/scan-stats")
    assert r.status_code == 200, r.text
    body = r.json()
    # 합산 필드(aggregated/skipped)는 없다 — 단일 리포트 서빙 계약
    assert "aggregated" not in body and "skipped" not in body
    assert body["summary"]["total_files"] == 7
    assert body["file_size_histogram"] == [
        {"bucket": "[0,4096]", "lower_inclusive": 0, "upper_inclusive": 4096,
         "count": 7}]
    assert body["time_histograms"]["atime"] == [
        {"bucket": "[0d,1d]", "min_age_days": 0, "max_age_days": 1, "bytes": 50}]
    assert body["generated_at_epoch"] == 1785805962
    assert body["broken_paths_total"] == 3
    assert body["broken_paths_limit"] == 100


def test_missing_request_404(tmp_path):
    client = _client(tmp_path, tmp_path / "artifacts")
    _admin(client)
    r = client.get("/api/admin/requests/nope/scan-stats")
    assert r.status_code == 404 and r.json()["detail"] == "request_not_found"


def test_non_scan_request_404_no_scan_report(tmp_path):
    art_base = tmp_path / "artifacts"
    client = _client(tmp_path, art_base)
    _admin(client)
    rid = _request(client, operation="sync")
    # sync 요청엔 dscan 리포트가 애초에 없다 — "성공 scan 리포트 없음"과 같은
    # 사실이라 한 코드(no_scan_report)로 답한다.
    r = client.get(f"/api/admin/requests/{rid}/scan-stats")
    assert r.status_code == 404 and r.json()["detail"] == "no_scan_report"


def test_scan_without_succeeded_job_404_no_scan_report(tmp_path):
    client = _client(tmp_path, tmp_path / "artifacts")
    _admin(client)
    rid = _request(client)                       # 잡이 아직 없다(Pending)
    r = client.get(f"/api/admin/requests/{rid}/scan-stats")
    assert r.status_code == 404 and r.json()["detail"] == "no_scan_report"


def test_report_file_absent_404_no_scan_report(tmp_path):
    art_base = tmp_path / "artifacts"
    client = _client(tmp_path, art_base)
    _admin(client)
    rid = _request(client)
    _succeed_job(client, rid, art_base=art_base, write_report=False)
    r = client.get(f"/api/admin/requests/{rid}/scan-stats")
    assert r.status_code == 404 and r.json()["detail"] == "no_scan_report"


def test_unparseable_report_404_no_scan_report(tmp_path):
    art_base = tmp_path / "artifacts"
    client = _client(tmp_path, art_base)
    _admin(client)
    rid = _request(client)
    _succeed_job(client, rid, art_base=art_base, raw_report="{ not json")
    r = client.get(f"/api/admin/requests/{rid}/scan-stats")
    assert r.status_code == 404 and r.json()["detail"] == "no_scan_report"


def test_non_dict_json_404_no_scan_report(tmp_path):
    art_base = tmp_path / "artifacts"
    client = _client(tmp_path, art_base)
    _admin(client)
    rid = _request(client)
    _succeed_job(client, rid, art_base=art_base, raw_report="[1, 2]")
    # null·[]·"x" 도 유효한 JSON 이다 — dict 가 아니면 쓸 수 있는 리포트가 없다
    r = client.get(f"/api/admin/requests/{rid}/scan-stats")
    assert r.status_code == 404 and r.json()["detail"] == "no_scan_report"


def test_truncated_report_503_too_large(tmp_path):
    """읽기 상한(256 KiB) 초과 리포트는 꼬리만 와서 JSON 이 깨진다. 단일 리포트
    서빙이라 "없음"으로 뭉개면 거짓말 — scan_path_stats 선례와 동일하게 503 으로
    운영자가 고칠 수 있게 드러낸다(합산이던 구 배치 라우트만 skipped 였다)."""
    art_base = tmp_path / "artifacts"
    client = _client(tmp_path, art_base)
    _admin(client)
    rid = _request(client)
    big = _report()
    big["broken_paths"] = [{"path": f"/cephfs/dms/team/f{i}", "reasons": ["missing"]}
                           for i in range(6000)]
    raw = json.dumps(big)
    assert len(raw.encode()) > 256 * 1024
    _succeed_job(client, rid, art_base=art_base, raw_report=raw)
    r = client.get(f"/api/admin/requests/{rid}/scan-stats")
    assert r.status_code == 503 and r.json()["detail"] == "scan_report_too_large"


def test_shape_projection_never_leaks_paths(tmp_path):
    art_base = tmp_path / "artifacts"
    client = _client(tmp_path, art_base)
    _admin(client)
    rid = _request(client)
    secret = "/cephfs/dms/team/secret.txt"
    planted = _report()
    planted["summary"]["largest_file"] = secret
    planted["file_size_histogram"][0]["example_path"] = secret
    planted["file_size_histogram"].append({"bucket": secret, "count": 1})
    planted["time_histograms"]["atime"][0]["oldest_path"] = secret
    planted["time_histograms"][secret] = [{"bucket": "[0d,1d]", "bytes": 1}]
    _succeed_job(client, rid, art_base=art_base, report=planted)
    r = client.get(f"/api/admin/requests/{rid}/scan-stats")
    assert r.status_code == 200
    raw = r.text
    assert "secret.txt" not in raw
    assert "/cephfs" not in raw
    assert "largest_file" not in raw
    body = r.json()
    assert body["summary"]["total_files"] == 7      # 수치는 남는다
    assert body["broken_paths_total"] == 3


def test_old_report_without_broken_total_keeps_null(tmp_path):
    art_base = tmp_path / "artifacts"
    client = _client(tmp_path, art_base)
    _admin(client)
    rid = _request(client)
    old = _report()
    del old["broken_paths_total"], old["broken_paths_limit"]
    _succeed_job(client, rid, art_base=art_base, report=old)
    r = client.get(f"/api/admin/requests/{rid}/scan-stats")
    assert r.status_code == 200
    # 구형 리포트(총계 미기록)는 None — 0(파손 없음)과 다르다(null≠0)
    assert r.json()["broken_paths_total"] is None
    assert r.json()["broken_paths_limit"] is None


def test_requires_admin(tmp_path):
    client = _client(tmp_path, tmp_path / "artifacts")
    r = client.get("/api/admin/requests/x/scan-stats")
    assert r.status_code == 401


def test_batch_level_aggregate_route_removed(tmp_path):
    """배치 합산 라우트는 제거됐다(사용자 결정: "배치 전체에 대한 건 필요없어").
    라우트 자체가 없으므로 FastAPI 기본 404(detail="Not Found")다 — 사유 코드
    batch_not_found(존재하는 라우트의 도메인 404)와 다른 게 정직하다."""
    client = _client(tmp_path, tmp_path / "artifacts")
    _admin(client)
    r = client.get("/api/admin/batches/whatever/scan-stats")
    assert r.status_code == 404 and r.json()["detail"] == "Not Found"
