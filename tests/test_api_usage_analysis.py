"""사용량 분석 라우트(routes_usage.py) — scan 이력 시계열·타깃 검색.

픽스처 관례는 test_api_request_scan_stats 를 따른다(성공 잡 materialize +
execution/dscan-report.json 직접 기록)."""
import json

from fastapi.testclient import TestClient

from dms.config import Settings
from dms.db import Database
from dms.domain import DataJobState
from dms.migrations import migrate


def _report(*, mtime_bytes=(30, 20), atime_bytes=(50,), epoch=1785805962,
            files=7):
    def buckets(vals):
        return [{"bucket": f"[{i}d,{i + 1}d]", "min_age_days": i,
                 "max_age_days": i + 1, "bytes": b} for i, b in enumerate(vals)]
    return {
        "directory": "/cephfs/dms/team",
        "generated_at_epoch": epoch,
        "summary": {"total_entries": 10, "total_files": files,
                    "total_directories": 3, "total_symlinks": 0,
                    "total_other": 0, "scan_errors": 0},
        "file_size_histogram": [{"bucket": "[0,4096]", "lower_inclusive": 0,
                                 "upper_inclusive": 4096, "count": files}],
        "time_histograms": {"atime": buckets(atime_bytes),
                            "mtime": buckets(mtime_bytes), "ctime": []},
        "broken_paths_total": 0, "broken_paths_limit": 100, "broken_paths": [],
    }


def _client(tmp_path, artifact_base):
    from dms.api.app import create_app
    db = Database.connect(f"sqlite:///{tmp_path}/test.db")
    migrate(db)
    settings = Settings(database_url="unused", shared_token="tok-shared",
                        admin_token="tok-admin", session_secret="sess-secret",
                        artifact_base_uri=f"file://{artifact_base}")
    return TestClient(create_app(settings, db))


def _login(client, username, role):
    client.app.state.repos.accounts.create(username, "pw", role, actor="t")
    client.post("/api/auth/login", json={"username": username, "password": "pw"})


def _scan_job(client, *, storage="s1", target="team", requester="alice",
              owner="alice", report=None, raw_report=None, art_base=None,
              write_report=True):
    repos = client.app.state.repos
    rid = repos.requests.create(operation="scan", requester_id=requester,
        actor=requester, resource_key=f"k:{storage}:{target}:{requester}:{id(report)}",
        payload={"storage": storage, "target": target}, priority="mid")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan_id, operation="scan",
        priority="mid", storage_name=storage, target=target, options={},
        tool="dscan", worker_pool={"identity": {"username": owner, "uid": 1,
                                                "gid": 1}},
        precondition={}, actor="planner")
    repos.data_jobs.set_job_state(jid, DataJobState.SUCCEEDED, actor="stepper")
    repos.requests.finalize_from_job(rid, DataJobState.SUCCEEDED, actor="stepper")
    if write_report:
        d = art_base / jid / "execution"
        d.mkdir(parents=True)
        body = raw_report if raw_report is not None else json.dumps(
            _report() if report is None else report)
        (d / "dscan-report.json").write_text(body)
    return jid, rid


# ---- /api/admin/usage/scan-targets ----

def test_targets_grouped_across_requesters_with_counts(tmp_path):
    art = tmp_path / "artifacts"
    client = _client(tmp_path, art)
    _login(client, "admin", "admin")
    _scan_job(client, target="team", requester="alice", art_base=art)
    _scan_job(client, target="team", requester="bob", art_base=art,
              report=_report(epoch=1785805999))
    _scan_job(client, target="other", requester="alice", art_base=art)
    r = client.get("/api/admin/usage/scan-targets")
    assert r.status_code == 200, r.text
    rows = r.json()
    by_target = {row["target"]: row for row in rows}
    # 요청자와 무관하게 (storage, target) 로 합쳐진다 — 사용자 요청의 핵심 계약
    assert by_target["team"]["scan_count"] == 2
    assert by_target["other"]["scan_count"] == 1
    assert all(row["storage_name"] == "s1" for row in rows)


def test_targets_search_substring_and_like_escape(tmp_path):
    art = tmp_path / "artifacts"
    client = _client(tmp_path, art)
    _login(client, "admin", "admin")
    _scan_job(client, target="artifacts/deep", art_base=art)
    _scan_job(client, target="team", art_base=art)
    got = client.get("/api/admin/usage/scan-targets", params={"q": "artif"}).json()
    assert [r["target"] for r in got] == ["artifacts/deep"]
    # LIKE 메타문자는 리터럴이다 — '%' 하나로 전부 매치되면 검색이 거짓말이 된다
    assert client.get("/api/admin/usage/scan-targets",
                      params={"q": "%"}).json() == []


def test_targets_requires_admin(tmp_path):
    client = _client(tmp_path, tmp_path / "artifacts")
    _login(client, "user1", "user")
    assert client.get("/api/admin/usage/scan-targets").status_code == 403


# ---- /api/admin/usage/scan-history ----

def test_history_points_ascending_with_projection(tmp_path):
    art = tmp_path / "artifacts"
    client = _client(tmp_path, art)
    _login(client, "admin", "admin")
    j1, r1 = _scan_job(client, target="team", owner="alice", art_base=art,
                       report=_report(mtime_bytes=(30, 20), epoch=100))
    j2, r2 = _scan_job(client, target="team", owner="bob", art_base=art,
                       report=_report(mtime_bytes=(70, 30), epoch=200))
    r = client.get("/api/admin/usage/scan-history",
                   params={"storage": "s1", "target": "team"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["storage_name"] == "s1" and body["target"] == "team"
    assert body["skipped_unreadable"] == 0
    pts = body["points"]
    assert [p["job_id"] for p in pts] == [j1, j2]        # 완료 오름차순
    assert [p["total_bytes"] for p in pts] == [50, 100]  # mtime 합 = 실 사용량
    assert pts[0]["requester"] == "alice" and pts[1]["requester"] == "bob"
    assert pts[0]["summary"]["total_files"] == 7
    assert pts[0]["generated_at_epoch"] == 100
    assert pts[0]["request_id"] == r1 and pts[1]["request_id"] == r2
    # 온도 히스토그램은 모양 투영 그대로 실린다(화면이 스택 바를 그린다)
    assert pts[0]["time_histograms"]["mtime"][0]["bytes"] == 30


def test_history_skips_unreadable_reports_and_counts_them(tmp_path):
    art = tmp_path / "artifacts"
    client = _client(tmp_path, art)
    _login(client, "admin", "admin")
    _scan_job(client, target="team", art_base=art)                     # 정상
    _scan_job(client, target="team", art_base=art, raw_report="{bro") # 깨짐
    _scan_job(client, target="team", art_base=art, write_report=False) # 부재
    body = client.get("/api/admin/usage/scan-history",
                      params={"storage": "s1", "target": "team"}).json()
    assert len(body["points"]) == 1
    assert body["skipped_unreadable"] == 2


def test_history_window_fields_tell_truncation(tmp_path):
    # 창 고지: 행 수가 limit 을 꽉 채우면 그 너머가 있을 수 있다(window_full).
    art = tmp_path / "artifacts"
    client = _client(tmp_path, art)
    _login(client, "admin", "admin")
    for _ in range(3):
        _scan_job(client, target="team", art_base=art)
    body = client.get("/api/admin/usage/scan-history",
                      params={"storage": "s1", "target": "team", "limit": 2}).json()
    assert body["window_limit"] == 2 and body["window_full"] is True
    assert len(body["points"]) == 2
    body = client.get("/api/admin/usage/scan-history",
                      params={"storage": "s1", "target": "team", "limit": 30}).json()
    assert body["window_full"] is False


def test_history_unknown_target_is_empty_not_404(tmp_path):
    client = _client(tmp_path, tmp_path / "artifacts")
    _login(client, "admin", "admin")
    body = client.get("/api/admin/usage/scan-history",
                      params={"storage": "s1", "target": "nope"}).json()
    assert body["points"] == [] and body["skipped_unreadable"] == 0


def test_history_exact_target_no_subtree_mixing(tmp_path):
    # team 과 team/sub 는 다른 시계열이다 — covers() 류 포함 관계로 섞으면
    # 부모 이력에 자식 스캔이 끼어 용량이 요동친다.
    art = tmp_path / "artifacts"
    client = _client(tmp_path, art)
    _login(client, "admin", "admin")
    _scan_job(client, target="team", art_base=art)
    _scan_job(client, target="team/sub", art_base=art)
    body = client.get("/api/admin/usage/scan-history",
                      params={"storage": "s1", "target": "team"}).json()
    assert len(body["points"]) == 1


def test_history_requires_admin(tmp_path):
    client = _client(tmp_path, tmp_path / "artifacts")
    _login(client, "user1", "user")
    assert client.get("/api/admin/usage/scan-history",
                      params={"storage": "s1", "target": "t"}).status_code == 403
