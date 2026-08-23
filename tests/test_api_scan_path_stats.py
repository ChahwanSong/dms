"""GET /api/user/scan-paths/{id}/stats — 커버링 scan 리포트에서 뽑은 화이트리스트
통계만 노출한다. dscan 리포트의 broken_paths(구체 파일 경로)·directory(절대
마운트 경로)·thresholds는 절대 응답에 섞이면 안 된다. 구형 리포트의 oldest·top_k
(신 dscan 1b93d54에서 스키마 삭제)도 금지 목록에 남긴다 — 아티팩트 디렉터리에는
구형 리포트가 계속 존재한다(상위 스펙 §8). broken_paths_total/limit(숫자 총계)만
신규 노출 — 구형 리포트(키 부재)는 None(미기록, null≠0)."""
import json

from dms.config import Settings
from dms.db import Database
from dms.domain import DataJobState
from dms.migrations import migrate
from fastapi.testclient import TestClient


# 실측 신 스키마(dscan 1b93d54): top_k·oldest 없음, broken_paths_total(정확 총계)·
# broken_paths_limit·summary.scan_errors 신설. broken_paths 는 {path, reasons} 목록.
REPORT = {
    "directory": "/cephfs/dms/team",
    "generated_at_epoch": 1785805962,
    "thresholds": {"abnormal_size_bytes": 1},
    "summary": {"total_entries": 10, "total_files": 7, "total_directories": 3,
                "total_symlinks": 0, "total_other": 0, "scan_errors": 0},
    "file_size_histogram": [{"bucket": "[0,4096]", "lower_inclusive": 0,
                            "upper_inclusive": 4096, "count": 7}],
    "time_histograms": {"atime": [{"bucket": "[0d,1d]", "min_age_days": 0,
                                   "max_age_days": 1, "bytes": 50}],
                        "mtime": [], "ctime": []},
    "broken_paths_total": 3,
    "broken_paths_limit": 100,
    "broken_paths": [{"path": "/cephfs/dms/team/secret.txt",
                      "reasons": ["missing"]}],
}

# 구형 리포트(top_k 시절 dscan) — 아티팩트에 그대로 남아 있다. stats 는 여전히
# 동작해야 하고 broken 필드는 None(미기록)이어야 한다.
OLD_REPORT = {
    "directory": "/cephfs/dms/team",
    "generated_at_epoch": 1785805962,
    "top_k": 10,
    "thresholds": {"abnormal_size_bytes": 1},
    "summary": {"total_entries": 10, "total_files": 7, "total_directories": 3,
                "total_symlinks": 0, "total_other": 0},
    "file_size_histogram": [{"bucket": "[0,4096]", "lower_inclusive": 0,
                            "upper_inclusive": 4096, "count": 7}],
    "time_histograms": {"atime": [], "mtime": [], "ctime": []},
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
             created_at=None, write_report=True, art_base=None, report=None,
             raw_report=None):
    """target을 대상으로 한 성공(Succeeded) scan 잡을 만든다. write_report이면
    art_base/<job_id>/execution/dscan-report.json 에 REPORT를 써 둔다.
    created_at을 주면 created_at·updated_at을 그 값으로 강제해 "최신" 후보를
    결정론적으로 통제한다(후보 정렬은 updated_at 기준이다)."""
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
        db.execute(
            "UPDATE data_jobs SET created_at = :c, updated_at = :c WHERE job_id = :j",
            {"c": created_at, "j": jid})
    if write_report:
        assert art_base is not None
        d = art_base / jid / "execution"
        d.mkdir(parents=True)
        body = raw_report if raw_report is not None else json.dumps(
            REPORT if report is None else report)
        (d / "dscan-report.json").write_text(body)
    return jid


def _register(client, storage_name="ceph-a", path="team"):
    admin = {"Authorization": "Bearer tok-shared"}
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
                                "file_size_histogram", "time_histograms",
                                "broken_paths_total", "broken_paths_limit",
                                "total_bytes"}
    # 실 사용량(2026-08-23): mtime 이 비어 atime 합(50)으로 폴백 -- 숫자 하나라
    # 화이트리스트("집계 통계뿐") 안이다
    assert body["total_bytes"] == 50
    # broken_paths(경로 목록)는 여전히 금지 — 총계(숫자)만 나간다. oldest·top_k는
    # 신 스키마엔 없지만 구형 리포트 방어로 금지 목록에 남긴다.
    for forbidden_key in ("oldest", "broken_paths", "directory", "thresholds", "top_k"):
        assert forbidden_key not in body

    raw = r.text
    assert "secret.txt" not in raw
    assert "/cephfs" not in raw


def test_broken_totals_are_exposed_as_numbers(tmp_path):
    """신 dscan(1b93d54)의 정확 총계·보관 상한은 숫자라 노출 계약("집계 통계뿐")
    안이다 — 경로 표본(broken_paths)은 여전히 금지."""
    art_base = tmp_path / "artifacts"
    client, db = _client(tmp_path, art_base)
    repos = client.app.state.repos
    _login(client, "alice")
    path_id = _register(client, "ceph-a", "team")
    _scan_job(repos, db, storage_name="ceph-a", target="team", art_base=art_base)

    r = client.get(f"/api/user/scan-paths/{path_id}/stats")
    assert r.status_code == 200
    body = r.json()
    assert body["broken_paths_total"] == 3
    assert body["broken_paths_limit"] == 100


def test_old_schema_report_still_serves_stats_with_broken_none(tmp_path):
    """구형 리포트(top_k·oldest 시절)는 아티팩트에 계속 존재한다 — 통계는 여전히
    나가고 broken 필드는 None(미기록). null≠0: 0은 "파손 없음"의 정상값이라
    구형 리포트를 0으로 뭉개면 거짓말이다."""
    art_base = tmp_path / "artifacts"
    client, db = _client(tmp_path, art_base)
    repos = client.app.state.repos
    _login(client, "alice")
    path_id = _register(client, "ceph-a", "team")
    _scan_job(repos, db, storage_name="ceph-a", target="team", art_base=art_base,
              report=OLD_REPORT)

    r = client.get(f"/api/user/scan-paths/{path_id}/stats")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["summary"]["total_entries"] == 10
    assert body["broken_paths_total"] is None
    assert body["broken_paths_limit"] is None
    for forbidden_key in ("oldest", "broken_paths", "top_k"):
        assert forbidden_key not in body
    raw = r.text
    assert "secret.txt" not in raw
    assert "/cephfs" not in raw


def test_nested_report_values_never_leak_paths(tmp_path):
    """화이트리스트는 **키**만 거른다 — 값 안쪽(summary의 새 필드, 버킷의 추가 필드,
    히스토그램 이름)은 전부 dscan이 정한다. 노출 계약은 "집계 통계뿐"이므로 모양으로
    투영해야 한다: 숫자와 구간 라벨만 남고 경로는 어디로도 새지 않는다."""
    art_base = tmp_path / "artifacts"
    client, db = _client(tmp_path, art_base)
    repos = client.app.state.repos
    _login(client, "alice")
    path_id = _register(client, "ceph-a", "team")
    secret = "/cephfs/dms/team/secret.txt"
    planted = json.loads(json.dumps(REPORT))
    planted["summary"]["largest_file"] = secret
    planted["file_size_histogram"][0]["example_path"] = secret
    planted["time_histograms"]["atime"][0]["oldest_path"] = secret
    planted["time_histograms"][secret] = [{"bucket": "[0d,1d]", "bytes": 1}]
    planted["file_size_histogram"].append({"bucket": secret, "count": 1})
    _scan_job(repos, db, storage_name="ceph-a", target="team", art_base=art_base,
              report=planted)

    r = client.get(f"/api/user/scan-paths/{path_id}/stats")
    assert r.status_code == 200
    raw = r.text
    assert "secret.txt" not in raw
    assert "/cephfs" not in raw
    assert "largest_file" not in raw
    assert "example_path" not in raw
    assert "oldest_path" not in raw
    body = r.json()
    # 집계 수치 자체는 그대로 남아야 한다.
    assert body["summary"]["total_files"] == 7
    assert body["file_size_histogram"][0]["bucket"] == "[0,4096]"
    assert body["file_size_histogram"][0]["count"] == 7
    assert body["time_histograms"]["atime"][0]["bytes"] == 50
    # 라벨이 경로인 버킷은 라벨만 잃고 수치는 남는다.
    assert body["file_size_histogram"][1] == {"count": 1}


def test_oversized_report_reports_503_not_a_false_no_covering_scan(tmp_path):
    """리포트가 읽기 상한(256 KiB)을 넘으면 read_artifact는 꼬리만 준다 — JSON이
    깨진다. 이걸 "커버하는 scan이 없다"로 뭉개면 거짓말이다."""
    art_base = tmp_path / "artifacts"
    client, db = _client(tmp_path, art_base)
    repos = client.app.state.repos
    _login(client, "alice")
    path_id = _register(client, "ceph-a", "team")
    big = json.loads(json.dumps(REPORT))
    # 신 스키마에서 리포트를 비대하게 만드는 건 broken_paths 표본이다(--broken-limit
    # 을 크게 준 scan) — 경로 문자열이 그대로 실린다.
    big["broken_paths"] = [{"path": f"/cephfs/dms/team/f{i}", "reasons": ["missing"]}
                           for i in range(6000)]
    raw = json.dumps(big)
    assert len(raw.encode()) > 256 * 1024
    _scan_job(repos, db, storage_name="ceph-a", target="team", art_base=art_base,
              raw_report=raw)

    r = client.get(f"/api/user/scan-paths/{path_id}/stats")
    assert r.status_code == 503
    assert r.json()["detail"] == "scan_report_too_large"


def test_artifact_reads_are_bounded_per_request(tmp_path, monkeypatch):
    """후보 상한(200)만으로는 부족하다 — 읽히지 않는 후보가 쌓이면 요청 1건이
    아티팩트 200번 읽기가 된다(동기 라우트, 스레드풀 공유). 읽기 횟수를 묶는다."""
    from dms.api import routes_scan_paths as mod

    art_base = tmp_path / "artifacts"
    client, db = _client(tmp_path, art_base)
    repos = client.app.state.repos
    _login(client, "alice")
    path_id = _register(client, "ceph-a", "team")
    for i in range(8):
        _scan_job(repos, db, storage_name="ceph-a", target="team",
                  created_at=f"2020-01-{i + 1:02d}T00:00:00Z", art_base=art_base)

    calls = []
    real = mod.read_artifact

    def counting(*a, **kw):
        calls.append(a)
        f = real(*a, **kw)
        f["content"] = "{ not json"          # 전부 못 읽는 후보로 만든다
        return f

    monkeypatch.setattr(mod, "read_artifact", counting)
    r = client.get(f"/api/user/scan-paths/{path_id}/stats")
    assert r.status_code == 404
    assert r.json()["detail"] == "no_covering_scan"
    assert len(calls) <= 5, f"read_artifact가 {len(calls)}번 호출됐다"


def test_valid_json_but_not_an_object_falls_through(tmp_path):
    """null·[]·"x" 는 전부 유효한 JSON이다 — 파싱은 되고 .get에서 터진다."""
    art_base = tmp_path / "artifacts"
    client, db = _client(tmp_path, art_base)
    repos = client.app.state.repos
    _login(client, "alice")
    path_id = _register(client, "ceph-a", "team")
    _scan_job(repos, db, storage_name="ceph-a", target="team",
              created_at="2020-01-01T00:00:00Z", art_base=art_base)
    _scan_job(repos, db, storage_name="ceph-a", target="team",
              created_at="2020-06-01T00:00:00Z", art_base=art_base, raw_report="null")

    r = client.get(f"/api/user/scan-paths/{path_id}/stats")
    assert r.status_code == 200, r.text
    assert r.json()["summary"]["total_entries"] == 10


def test_missing_or_wrong_shaped_fields_never_serve_null(tmp_path):
    """키가 없거나 모양이 다르면 None을 흘리지 않는다 — 클라이언트 타입은
    non-nullable이고, null이 렌더에 닿으면 화면 전체가 죽는다."""
    art_base = tmp_path / "artifacts"
    client, db = _client(tmp_path, art_base)
    repos = client.app.state.repos
    _login(client, "alice")
    path_id = _register(client, "ceph-a", "team")
    _scan_job(repos, db, storage_name="ceph-a", target="team", art_base=art_base,
              report={"summary": None, "file_size_histogram": "nope",
                      "time_histograms": ["nope"], "generated_at_epoch": "nope",
                      # 숫자 자리를 dscan이 문자열(경로일 수 있다)·bool로 채우면
                      # 모양 투영이 걸러 None — 경로가 총계 행세로 새지 않는다.
                      "broken_paths_total": "/cephfs/evil", "broken_paths_limit": True})

    r = client.get(f"/api/user/scan-paths/{path_id}/stats")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["summary"] == {}
    assert body["file_size_histogram"] == []
    assert body["time_histograms"] == {}
    assert body["generated_at_epoch"] is None
    assert body["broken_paths_total"] is None
    assert body["broken_paths_limit"] is None
    assert "/cephfs" not in r.text


def test_non_finite_numbers_do_not_break_serialization(tmp_path):
    """json.loads는 NaN/Infinity 리터럴을 받아들이지만 응답 직렬화는 거부한다."""
    art_base = tmp_path / "artifacts"
    client, db = _client(tmp_path, art_base)
    repos = client.app.state.repos
    _login(client, "alice")
    path_id = _register(client, "ceph-a", "team")
    _scan_job(repos, db, storage_name="ceph-a", target="team", art_base=art_base,
              raw_report='{"summary": {"total_files": NaN, "total_entries": 3}}')

    r = client.get(f"/api/user/scan-paths/{path_id}/stats")
    assert r.status_code == 200, r.text
    assert r.json()["summary"] == {"total_entries": 3}


def test_covered_by_target_is_normalized(tmp_path):
    art_base = tmp_path / "artifacts"
    client, db = _client(tmp_path, art_base)
    repos = client.app.state.repos
    _login(client, "alice")
    path_id = _register(client, "ceph-a", "team")
    _scan_job(repos, db, storage_name="ceph-a", target="./team/", art_base=art_base)

    r = client.get(f"/api/user/scan-paths/{path_id}/stats")
    assert r.status_code == 200, r.text
    assert r.json()["covered_by"] == {"target": "team", "exact": True}


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
    admin = {"Authorization": "Bearer tok-shared"}
    client.post("/api/admin/storages", json={
        "storage_name": "ceph-b", "mount_path": "/mnt/ceph-b",
        "managed_root": "/mnt/ceph-b/dms", "backend_type": "cephfs"}, headers=admin)
    _scan_job(repos, db, storage_name="ceph-b", target="team", art_base=art_base)

    r = client.get(f"/api/user/scan-paths/{path_id}/stats")
    assert r.status_code == 404
    assert r.json()["detail"] == "no_covering_scan"
