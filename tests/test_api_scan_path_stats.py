"""GET /api/user/scan-paths/{id}/stats — 커버링 scan 리포트에서 뽑은 화이트리스트
통계만 노출한다. dscan 리포트의 oldest(구체 파일 경로)·broken_paths·directory(절대
마운트 경로)·thresholds·top_k는 절대 응답에 섞이면 안 된다(상위 스펙 §8)."""
import json

from dms.config import Settings
from dms.db import Database
from dms.domain import DataJobState
from dms.migrations import migrate
from fastapi.testclient import TestClient


REPORT = {
    "directory": "/cephfs/dms/team",
    "generated_at_epoch": 1785805962,
    "top_k": 10,
    "thresholds": {"abnormal_size_bytes": 1},
    "summary": {"total_entries": 10, "total_files": 7, "total_directories": 3,
                "total_symlinks": 0, "total_other": 0},
    "file_size_histogram": [{"bucket": "[0,4096]", "lower_inclusive": 0,
                            "upper_inclusive": 4096, "count": 7}],
    "time_histograms": {"atime": [{"bucket": "[0d,1d]", "min_age_days": 0,
                                   "max_age_days": 1, "bytes": 50}],
                        "mtime": [], "ctime": []},
    "oldest": {"atime": [{"path": "/cephfs/dms/team/secret.txt", "type": "file",
                          "size_bytes": 1, "atime": 1, "mtime": 1, "ctime": 1}]},
    "broken_paths": ["/cephfs/dms/team/broken"],
}


def _client(tmp_path, artifact_base):
    from dms.api.app import create_app
    db = Database.connect(f"sqlite:///{tmp_path}/test.db")
    migrate(db)
    settings = Settings(database_url="unused", shared_token="tok-shared",
                        admin_token="tok-admin", session_secret="sess-secret",
                        artifact_base_uri=f"file://{artifact_base}")
    return TestClient(create_app(settings, db)), db


def _login(client, name="alice"):
    client.post("/api/auth/signup", json={"username": name, "password": "p"})
    client.post("/api/auth/login", json={"username": name, "password": "p"})


def _scan_job(repos, db, *, storage_name, target, requester="alice",
             created_at=None, write_report=True, art_base=None):
    """target을 대상으로 한 성공(Succeeded) scan 잡을 만든다. write_report이면
    art_base/<job_id>/execution/dscan-report.json 에 REPORT를 써 둔다.
    created_at을 주면 그 값으로 강제해 "최신" 후보를 결정론적으로 통제한다."""
    rid = repos.requests.create(
        operation="scan", requester_id=requester, actor=requester,
        resource_key=f"data.scan:{storage_name}:{target}:{requester}",
        payload={"storage": storage_name, "target": target}, priority="mid")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(
        rid, plan_id, operation="scan", priority="mid", storage_name=storage_name,
        target=target, options={}, tool="dscan", worker_pool={}, precondition={},
        actor="planner")
    repos.data_jobs.set_job_state(jid, DataJobState.SUCCEEDED, actor="stepper")
    if created_at is not None:
        db.execute("UPDATE data_jobs SET created_at = :c WHERE job_id = :j",
                   {"c": created_at, "j": jid})
    if write_report:
        assert art_base is not None
        d = art_base / jid / "execution"
        d.mkdir(parents=True)
        (d / "dscan-report.json").write_text(json.dumps(REPORT))
    return jid


def _register(client, storage_name="ceph-a", path="team"):
    admin = {"Authorization": "Bearer tok-shared", "x-dms-actor": "ops"}
    client.post("/api/admin/storages", json={
        "storage_name": storage_name, "mount_path": f"/mnt/{storage_name}",
        "managed_root": f"/mnt/{storage_name}/dms", "backend_type": "cephfs"},
        headers=admin)
    r = client.post("/api/user/scan-paths", json={
        "storage_name": storage_name, "path": path})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_exact_match_scan_returns_200_and_exact_true(tmp_path):
    art_base = tmp_path / "artifacts"
    client, db = _client(tmp_path, art_base)
    repos = client.app.state.repos
    _login(client, "alice")
    path_id = _register(client, "ceph-a", "team")
    _scan_job(repos, db, storage_name="ceph-a", target="team", art_base=art_base)

    r = client.get(f"/api/user/scan-paths/{path_id}/stats")
    assert r.status_code == 200
    body = r.json()
    assert body["covered_by"] == {"target": "team", "exact": True}


def test_response_is_whitelisted_and_never_leaks_paths(tmp_path):
    art_base = tmp_path / "artifacts"
    client, db = _client(tmp_path, art_base)
    repos = client.app.state.repos
    _login(client, "alice")
    path_id = _register(client, "ceph-a", "team")
    _scan_job(repos, db, storage_name="ceph-a", target="team", art_base=art_base)

    r = client.get(f"/api/user/scan-paths/{path_id}/stats")
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {"covered_by", "generated_at_epoch", "summary",
                                "file_size_histogram", "time_histograms"}
    for forbidden_key in ("oldest", "broken_paths", "directory", "thresholds", "top_k"):
        assert forbidden_key not in body

    raw = r.text
    assert "secret.txt" not in raw
    assert "/cephfs" not in raw


def test_parent_scan_covers_but_is_not_exact(tmp_path):
    art_base = tmp_path / "artifacts"
    client, db = _client(tmp_path, art_base)
    repos = client.app.state.repos
    _login(client, "alice")
    path_id = _register(client, "ceph-a", "team/sub")
    _scan_job(repos, db, storage_name="ceph-a", target="team", art_base=art_base)

    r = client.get(f"/api/user/scan-paths/{path_id}/stats")
    assert r.status_code == 200
    body = r.json()
    assert body["covered_by"] == {"target": "team", "exact": False}


def test_no_covering_scan_404(tmp_path):
    art_base = tmp_path / "artifacts"
    client, db = _client(tmp_path, art_base)
    repos = client.app.state.repos
    _login(client, "alice")
    path_id = _register(client, "ceph-a", "team")
    # 하위 디렉터리만 스캔한 잡 — 등록 경로 "team"을 커버하지 못한다.
    _scan_job(repos, db, storage_name="ceph-a", target="team/sub", art_base=art_base)

    r = client.get(f"/api/user/scan-paths/{path_id}/stats")
    assert r.status_code == 404
    assert r.json()["detail"] == "no_covering_scan"


def test_missing_report_on_newest_job_falls_through_to_next_candidate(tmp_path):
    art_base = tmp_path / "artifacts"
    client, db = _client(tmp_path, art_base)
    repos = client.app.state.repos
    _login(client, "alice")
    path_id = _register(client, "ceph-a", "team")
    # 더 오래된 잡: 리포트 있음.
    _scan_job(repos, db, storage_name="ceph-a", target="team",
             created_at="2020-01-01T00:00:00Z", art_base=art_base)
    # 더 최신 잡: 리포트가 없다 (job runner가 아직 안 썼거나 유실됨) — 다음 후보로 넘어가야 한다.
    _scan_job(repos, db, storage_name="ceph-a", target="team",
             created_at="2020-06-01T00:00:00Z", write_report=False, art_base=art_base)

    r = client.get(f"/api/user/scan-paths/{path_id}/stats")
    assert r.status_code == 200
    body = r.json()
    assert body["summary"]["total_entries"] == 10


def test_other_users_path_404(tmp_path):
    art_base = tmp_path / "artifacts"
    client, db = _client(tmp_path, art_base)
    repos = client.app.state.repos
    _login(client, "alice")
    path_id = _register(client, "ceph-a", "team")
    _scan_job(repos, db, storage_name="ceph-a", target="team", art_base=art_base)

    _login(client, "eve")
    r = client.get(f"/api/user/scan-paths/{path_id}/stats")
    assert r.status_code == 404
    assert r.json()["detail"] == "scan_path_not_found"


def test_scan_on_different_storage_does_not_cover(tmp_path):
    art_base = tmp_path / "artifacts"
    client, db = _client(tmp_path, art_base)
    repos = client.app.state.repos
    _login(client, "alice")
    path_id = _register(client, "ceph-a", "team")
    admin = {"Authorization": "Bearer tok-shared", "x-dms-actor": "ops"}
    client.post("/api/admin/storages", json={
        "storage_name": "ceph-b", "mount_path": "/mnt/ceph-b",
        "managed_root": "/mnt/ceph-b/dms", "backend_type": "cephfs"}, headers=admin)
    _scan_job(repos, db, storage_name="ceph-b", target="team", art_base=art_base)

    r = client.get(f"/api/user/scan-paths/{path_id}/stats")
    assert r.status_code == 404
    assert r.json()["detail"] == "no_covering_scan"
