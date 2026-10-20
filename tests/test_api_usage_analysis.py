"""사용량 분석 라우트(routes_usage.py) — scan 이력 시계열·타깃 검색.

픽스처 관례는 test_api_request_scan_stats 를 따른다(성공 잡 materialize +
execution/dscan-report.json 직접 기록)."""
import os
import json

import pytest
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
        tool="dscan", worker_pool={"identity": {"username": owner, "uid": os.getuid(),
                                                "gid": os.getgid()}},
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


# ---- 2026-10-02: 목록 컬럼(latest)·필터 분리·정렬·리포트 요약 캐시·내보내기 ----

def _at(client, jid, iso):
    # 같은 초에 끝난 잡은 최신 판정이 job_id(무작위)로 갈린다 -- 시각을 박아 결정적으로 만든다
    client.app.state.repos.data_jobs._db.execute(
        "UPDATE data_jobs SET updated_at = :t WHERE job_id = :j", {"t": iso, "j": jid})


def _digest_rows(client):
    return client.app.state.repos.data_jobs._db.query("SELECT job_id FROM scan_report_digests")


def test_targets_rows_carry_latest_scan_point(tmp_path):
    art = tmp_path / "artifacts"
    client = _client(tmp_path, art)
    _login(client, "admin", "admin")
    old, _ = _scan_job(client, target="team", art_base=art, report=_report(mtime_bytes=(10,), files=3))
    new, new_rid = _scan_job(client, target="team", art_base=art,
                             report=_report(mtime_bytes=(30, 20), atime_bytes=(40, 10), files=7))
    _at(client, old, "2026-09-01T00:00:00Z")
    _at(client, new, "2026-09-20T00:00:00Z")
    [row] = client.get("/api/admin/usage/scan-targets").json()
    assert row["first_scan_at"] == "2026-09-01T00:00:00Z" and row["last_scan_at"] == "2026-09-20T00:00:00Z"
    latest = row["latest"]
    # 최신 스캔의 실 사용량(mtime 합 50)·파일 수(summary.total_files)·온도(목록의 hot 비율 원천)
    assert latest["job_id"] == new and latest["request_id"] == new_rid
    assert latest["total_bytes"] == 50 and latest["summary"]["total_files"] == 7
    assert [b["bytes"] for b in latest["time_histograms"]["atime"]] == [40, 10]
    assert latest["report_readable"] is True and latest["requester"] == "alice"


def test_report_digest_is_cached_per_job(tmp_path):
    # 리포트는 성공 종단 잡의 불변 산출물 -- 한 번 투영하면 아티팩트를 다시 열지 않는다(목록·이력 공용 캐시)
    art = tmp_path / "artifacts"
    client = _client(tmp_path, art)
    _login(client, "admin", "admin")
    jid, _ = _scan_job(client, target="team", art_base=art)
    assert client.get("/api/admin/usage/scan-targets").json()[0]["latest"]["total_bytes"] == 50
    assert [r["job_id"] for r in _digest_rows(client)] == [jid]
    (art / jid / "execution" / "dscan-report.json").unlink()
    assert client.get("/api/admin/usage/scan-targets").json()[0]["latest"]["total_bytes"] == 50
    hist = client.get("/api/admin/usage/scan-history", params={"storage": "s1", "target": "team"}).json()
    assert len(hist["points"]) == 1 and hist["skipped_unreadable"] == 0


def test_unreadable_latest_report_is_not_cached_and_falls_back_to_db_bytes(tmp_path):
    # 못 읽은 리포트는 캐시하지 않는다(일시 오류를 영구 "모름"으로 굳히지 않게). 실 사용량만은 러너가 같은 규칙으로
    # 남긴 bytes_count 로 대신하고, 리포트 유래 값(파일 수·온도)은 모름이다.
    art = tmp_path / "artifacts"
    client = _client(tmp_path, art)
    _login(client, "admin", "admin")
    jid, _ = _scan_job(client, target="team", art_base=art, write_report=False)
    client.app.state.repos.data_jobs.set_artifact(jid, artifact_uri="file:///x",
                                                  result_summary={"files": 10, "bytes": 1234})
    latest = client.get("/api/admin/usage/scan-targets").json()[0]["latest"]
    assert latest["report_readable"] is False and latest["total_bytes"] == 1234
    assert latest["summary"] == {} and latest["time_histograms"] == {}
    assert _digest_rows(client) == []
    # 리포트가 나중에 생기면(일시 오류 회복) 다음 조회가 읽어 캐시한다
    d = art / jid / "execution"
    d.mkdir(parents=True)
    (d / "dscan-report.json").write_text(json.dumps(_report()))
    latest = client.get("/api/admin/usage/scan-targets").json()[0]["latest"]
    assert latest["report_readable"] is True and latest["total_bytes"] == 50
    assert len(_digest_rows(client)) == 1


def test_cached_digest_is_reprojected_on_read(tmp_path):
    # DB 가 신뢰 경계 -- 캐시 행이 오염돼도(경로 문자열·비수치) 응답엔 투영된 모양만 나간다
    art = tmp_path / "artifacts"
    client = _client(tmp_path, art)
    _login(client, "admin", "admin")
    jid, _ = _scan_job(client, target="team", art_base=art, write_report=False)
    client.app.state.repos.scan_digests.put(jid, {
        "summary": {"total_files": 3, "leak": "/secret/path"},
        "time_histograms": {"atime": [{"bucket": "/etc/shadow", "bytes": 5}], "../x": []},
        "generated_at_epoch": "nope"})
    latest = client.get("/api/admin/usage/scan-targets").json()[0]["latest"]
    assert latest["summary"] == {"total_files": 3}
    assert latest["time_histograms"] == {"atime": [{"bytes": 5}]}
    assert latest["generated_at_epoch"] is None and latest["report_readable"] is True


def test_digest_put_is_noop_for_a_deleted_job(tmp_path, monkeypatch):
    # 2026-10-08 요청 삭제 선행 보강: 사용량 화면이 잡 목록을 읽은 뒤 요청 삭제가 커밋되면 put 이 지워진 잡의 요약을
    # 다시 넣던 경합 -- 잡 행이 없으면 no-op 이다(있는 잡은 지금처럼 캐시된다 -- test_report_digest_is_cached_per_job).
    import dms.api.routes_usage as routes_usage
    art = tmp_path / "artifacts"
    client = _client(tmp_path, art)
    _login(client, "admin", "admin")
    jid, _ = _scan_job(client, target="team", art_base=art)
    repos = client.app.state.repos
    repos.scan_digests.put("0" * 32, {"summary": {"total_files": 1}})       # 없는 잡
    assert _digest_rows(client) == []
    real = routes_usage._read_digest

    def read_then_delete(base, job):
        digest = real(base, job)                                            # 목록은 이미 읽혔다
        repos.data_jobs._db.execute("DELETE FROM data_jobs WHERE job_id = :j", {"j": job["job_id"]})
        return digest
    monkeypatch.setattr(routes_usage, "_read_digest", read_then_delete)
    assert client.get("/api/admin/usage/scan-targets").status_code == 200
    assert _digest_rows(client) == []


def test_targets_storage_and_path_filters_combine(tmp_path):
    art = tmp_path / "artifacts"
    client = _client(tmp_path, art)
    _login(client, "admin", "admin")
    _scan_job(client, storage="s1", target="proj/a", art_base=art)
    _scan_job(client, storage="s1", target="team/b", art_base=art)
    _scan_job(client, storage="s10", target="proj/c", art_base=art)
    get = lambda **p: sorted((r["storage_name"], r["target"])
                             for r in client.get("/api/admin/usage/scan-targets", params=p).json())
    # 스토리지는 정확히 일치(s1 이 s10 을 물지 않는다), 경로는 부분 문자열, 둘은 함께 걸린다
    assert get(storage="s1") == [("s1", "proj/a"), ("s1", "team/b")]
    assert get(path="proj") == [("s1", "proj/a"), ("s10", "proj/c")]
    assert get(storage="s1", path="proj") == [("s1", "proj/a")]
    assert get(storage="s1", path="%") == []                  # LIKE 메타문자는 리터럴
    assert get(q="s10") == [("s10", "proj/c")]                # 구 단일 검색(q) 호환


def test_targets_order_is_applied_before_limit(tmp_path):
    # 오름차순은 "가장 오래 안 본 타깃"이 보여야 한다 -- 최신 N개를 뒤집은 것이 아니다
    art = tmp_path / "artifacts"
    client = _client(tmp_path, art)
    _login(client, "admin", "admin")
    for i, t in enumerate(["t1", "t2", "t3"]):
        jid, _ = _scan_job(client, target=t, art_base=art)
        _at(client, jid, f"2026-09-0{i + 1}T00:00:00Z")
    get = lambda **p: [r["target"] for r in client.get("/api/admin/usage/scan-targets", params=p).json()]
    assert get(order="desc", limit=2) == ["t3", "t2"]
    assert get(order="asc", limit=2) == ["t1", "t2"]
    assert client.get("/api/admin/usage/scan-targets", params={"order": "sideways"}).status_code == 422


def test_export_rows_carry_latest_and_previous_for_every_target(tmp_path):
    art = tmp_path / "artifacts"
    client = _client(tmp_path, art)
    _login(client, "admin", "admin")
    a1, _ = _scan_job(client, target="team", art_base=art, report=_report(mtime_bytes=(10,)))
    a2, _ = _scan_job(client, target="team", art_base=art, report=_report(mtime_bytes=(70,)))
    b1, _ = _scan_job(client, storage="s2", target="solo", art_base=art)
    # 직전 스캔은 리포트를 새로 읽지 않는다 -- 캐시에 없으면 러너가 DB 에 남긴 bytes_count(set_artifact 는
    # updated_at 도 미므로 시각 고정보다 먼저)
    client.app.state.repos.data_jobs.set_artifact(a1, artifact_uri="file:///x", result_summary={"bytes": 10})
    _at(client, a1, "2026-09-01T00:00:00Z")
    _at(client, a2, "2026-09-02T00:00:00Z")
    _at(client, b1, "2026-08-01T00:00:00Z")
    body = client.get("/api/admin/usage/export").json()
    assert body["count"] == 2 and body["truncated"] is False and body["generated_at"]
    team, solo = body["rows"]                                  # 기본 최근 스캔 내림차순
    assert (team["target"], team["scan_count"]) == ("team", 2)
    assert team["latest"]["job_id"] == a2 and team["latest"]["total_bytes"] == 70
    assert team["previous"]["job_id"] == a1 and team["previous"]["total_bytes"] == 10
    # previous 는 증감용 값만(행 크기 -- 리뷰: 1만 행 전량이면 api 메모리 한도에 닿았다)
    assert set(team["previous"]) == {"job_id", "request_id", "finished_at", "total_bytes"}
    # 내보내기는 리포트 전체 요약을 싣는다 -- 파일 크기 분포·파손 경로 수까지
    assert team["latest"]["file_size_histogram"][0]["count"] == 7
    assert team["latest"]["broken_paths_total"] == 0
    assert solo["previous"] is None
    # 필터·정렬은 목록과 같다
    only = client.get("/api/admin/usage/export", params={"storage": "s2", "order": "asc"}).json()
    assert [r["target"] for r in only["rows"]] == ["solo"]


def test_export_truncation_is_told(tmp_path, monkeypatch):
    from dms.api import routes_usage
    monkeypatch.setattr(routes_usage, "_WINDOW_LIMIT", 1)
    art = tmp_path / "artifacts"
    client = _client(tmp_path, art)
    _login(client, "admin", "admin")
    _scan_job(client, target="t1", art_base=art)
    _scan_job(client, target="t2", art_base=art)
    body = client.get("/api/admin/usage/export").json()
    assert body["count"] == 1 and body["truncated"] is True


def test_export_requires_admin(tmp_path):
    client = _client(tmp_path, tmp_path / "artifacts")
    _login(client, "user1", "user")
    assert client.get("/api/admin/usage/export").status_code == 403



def test_export_previous_prefers_cached_digest_over_db_bytes(tmp_path):
    art = tmp_path / "artifacts"
    client = _client(tmp_path, art)
    _login(client, "admin", "admin")
    a1, _ = _scan_job(client, target="team", art_base=art, report=_report(mtime_bytes=(10,)))
    a2, _ = _scan_job(client, target="team", art_base=art, report=_report(mtime_bytes=(70,)))
    client.app.state.repos.data_jobs.set_artifact(a1, artifact_uri="file:///x", result_summary={"bytes": 999})
    _at(client, a1, "2026-09-01T00:00:00Z")
    _at(client, a2, "2026-09-02T00:00:00Z")
    # 이력 화면이 두 리포트를 이미 캐시했다 -- 내보내기의 직전 값은 그 캐시(리포트 규칙)를 쓴다
    client.get("/api/admin/usage/scan-history", params={"storage": "s1", "target": "team"})
    [team] = client.get("/api/admin/usage/export").json()["rows"]
    assert team["previous"]["total_bytes"] == 10


def test_deeply_nested_report_is_unreadable_not_a_500(tmp_path):
    # 요청자는 자기 잡의 아티팩트를 바꿀 수 있다(artifact_files 위협 모델) -- "[[[[…" 는 json 디코더가 ValueError 가
    # 아닌 RecursionError 를 던진다. 그 한 타깃이 관리자 목록·내보내기·이력을 500 으로 막으면 안 된다(리뷰 재현).
    art = tmp_path / "artifacts"
    client = _client(tmp_path, art)
    _login(client, "admin", "admin")
    _scan_job(client, target="good", art_base=art)
    _scan_job(client, target="evil", art_base=art, raw_report="[" * 100000 + "]" * 100000)
    r = client.get("/api/admin/usage/scan-targets")
    assert r.status_code == 200
    evil = next(row for row in r.json() if row["target"] == "evil")
    assert evil["latest"]["report_readable"] is False
    assert client.get("/api/admin/usage/export").status_code == 200
    h = client.get("/api/admin/usage/scan-history", params={"storage": "s1", "target": "evil"}).json()
    assert h["points"] == [] and h["skipped_unreadable"] == 1


@pytest.mark.parametrize("sort", ["last_scan", "bytes"])
def test_export_reads_jobs_of_selected_targets_in_chunks(tmp_path, monkeypatch, sort):
    # 수천 타깃 내보내기는 타깃을 묶음으로 나눠 묻고 흘린다(2026-10-20 _CHUNK) -- 묶음 경계에서 빠지거나 겹치는 타깃이
    # 없어야 한다(지표 정렬은 값 계산도 묶음이다). 스토리지가 섞인 「전체」(storage 없는 옛 탭)도 스토리지별로 나뉘어 묻는다.
    from dms.api import routes_usage
    from dms.repositories import data_jobs
    monkeypatch.setattr(routes_usage, "_CHUNK", 2)
    monkeypatch.setattr(data_jobs, "_IN_CHUNK", 2)
    art = tmp_path / "artifacts"
    client = _client(tmp_path, art)
    _login(client, "admin", "admin")
    for storage, t in [("s1", "a"), ("s2", "b"), ("s1", "c"), ("s2", "d"), ("s1", "e")]:
        _scan_job(client, storage=storage, target=t, art_base=art)
    rows = client.get("/api/admin/usage/export", params={"sort": sort}).json()["rows"]
    assert sorted((r["storage_name"], r["target"]) for r in rows) == [
        ("s1", "a"), ("s1", "c"), ("s1", "e"), ("s2", "b"), ("s2", "d")]
    assert all(r["latest"]["total_bytes"] == 50 for r in rows)


def test_scan_storages_lists_names_with_scan_history(tmp_path):
    # 필터 선택지: 등록이 지워진 스토리지 이름으로 남은 이력도 좁힐 수 있어야 한다(화면이 등록 목록과 합친다)
    art = tmp_path / "artifacts"
    client = _client(tmp_path, art)
    _login(client, "admin", "admin")
    _scan_job(client, storage="s2", target="a", art_base=art)
    _scan_job(client, storage="gone", target="b", art_base=art)
    assert client.get("/api/admin/usage/scan-storages").json() == ["gone", "s2"]


# ---- 2026-10-19: 목록 지표 정렬(실 사용량·파일 수·hot 비율·스캔 횟수) ----

def _metric_report(*, total, files, hot_bytes, cold_bytes):
    """실 사용량(mtime 합) = total, 파일 수 = files, atime 은 hot(≤180일)/cold(>180일) 바이트로 hot 비율을 정한다."""
    r = _report(mtime_bytes=(total,), files=files)
    r["time_histograms"]["atime"] = [
        {"bucket": "[0d,7d]", "min_age_days": 0, "max_age_days": 7, "bytes": hot_bytes},
        {"bucket": "[181d,365d]", "min_age_days": 181, "max_age_days": 365, "bytes": cold_bytes}]
    return r


def _metric_client(tmp_path):
    """대상 넷: 가장 큰 「old」 는 가장 오래전에 스캔됐다 -- limit 이 정렬 **전에** 걸리면(받은 묶음만 정렬) 안 보인다.
    「broken」 은 리포트를 못 읽어(작성 안 함) 파일 수·hot 이 모름이고, 실 사용량도 DB 값이 없어 모름이다."""
    art = tmp_path / "artifacts"
    client = _client(tmp_path, art)
    _login(client, "admin", "admin")
    spec = [("old", "2026-09-01", dict(total=900, files=5, hot_bytes=10, cold_bytes=90)),
            ("mid", "2026-09-05", dict(total=300, files=50, hot_bytes=50, cold_bytes=50)),
            ("new", "2026-09-09", dict(total=100, files=20, hot_bytes=90, cold_bytes=10))]
    for target, day, m in spec:
        jid, _ = _scan_job(client, target=target, art_base=art, report=_metric_report(**m))
        _at(client, jid, f"{day}T00:00:00Z")
    jid, _ = _scan_job(client, target="broken", art_base=art, write_report=False)
    _at(client, jid, "2026-09-10T00:00:00Z")
    return client


def test_metric_sort_ranks_the_whole_window_before_the_limit(tmp_path):
    client = _metric_client(tmp_path)
    get = lambda **p: [r["target"] for r in client.get("/api/admin/usage/scan-targets", params=p).json()]  # noqa: E731
    # 가장 큰 「old」 는 최근 스캔 순으로는 맨 뒤지만, 실 사용량 정렬에선 limit 안 맨 위다
    assert get(sort="bytes", order="desc", limit=2) == ["old", "mid"]
    assert get(sort="bytes", order="asc") == ["new", "mid", "old", "broken"]       # 모름은 방향과 상관없이 맨 뒤
    assert get(sort="files", order="desc") == ["mid", "new", "old", "broken"]
    assert get(sort="hot", order="desc") == ["new", "mid", "old", "broken"]
    assert get(sort="hot", order="asc") == ["old", "mid", "new", "broken"]
    # 응답 모양은 그대로다(latest 를 실은 행 배열)
    [top] = client.get("/api/admin/usage/scan-targets", params={"sort": "bytes", "limit": 1}).json()
    assert top["latest"]["total_bytes"] == 900 and top["latest"]["summary"]["total_files"] == 5


def test_scan_count_sort_is_sql_and_invalid_sort_is_rejected(tmp_path):
    art = tmp_path / "artifacts"
    client = _client(tmp_path, art)
    _login(client, "admin", "admin")
    for target, n, day in (("once", 1, "2026-09-09"), ("thrice", 3, "2026-09-01"), ("twice", 2, "2026-09-05")):
        for i in range(n):
            jid, _ = _scan_job(client, target=target, art_base=art)
            _at(client, jid, f"{day}T0{i}:00:00Z")
    get = lambda **p: [r["target"] for r in client.get("/api/admin/usage/scan-targets", params=p).json()]  # noqa: E731
    assert get(sort="scans", order="desc", limit=2) == ["thrice", "twice"]
    assert get(sort="scans", order="asc") == ["once", "twice", "thrice"]
    assert client.get("/api/admin/usage/scan-targets", params={"sort": "size"}).status_code == 422
    assert client.get("/api/admin/usage/export", params={"sort": "size"}).status_code == 422


def test_export_follows_the_same_sort(tmp_path):
    client = _metric_client(tmp_path)
    rows = client.get("/api/admin/usage/export", params={"sort": "bytes", "order": "desc"}).json()["rows"]
    assert [r["target"] for r in rows] == ["old", "mid", "new", "broken"]
    rows = client.get("/api/admin/usage/export", params={"sort": "hot", "order": "asc"}).json()["rows"]
    assert [r["target"] for r in rows] == ["old", "mid", "new", "broken"]


# ---- 2026-10-20: 스토리지별 분할·창 1만·묶음 스트리밍 내보내기·탭 요약 ----
# 옛 구현과의 응답 동일성(바이트)과 두 방언의 새 SQL 은 tests/test_usage_equivalence.py 가 본다.

def test_window_limit_is_ten_thousand_and_told_by_the_storages_summary(tmp_path):
    from dms.api import routes_usage
    assert routes_usage._WINDOW_LIMIT == 10000 and routes_usage._CHUNK == 500
    client = _client(tmp_path, tmp_path / "artifacts")
    _login(client, "admin", "admin")
    assert client.get("/api/admin/usage/storages").json() == {"window_limit": 10000, "storages": []}


def _spy(monkeypatch, obj, name, calls):
    real = getattr(obj, name)

    def spy(*args, **kwargs):
        calls.append((args, kwargs))
        return real(*args, **kwargs)
    monkeypatch.setattr(obj, name, spy)


def test_storage_scope_keeps_other_storages_out_of_the_window_and_the_response(tmp_path, monkeypatch):
    # 「필터 및 서버의 스캔도 각 스토리지 별로」: storage 를 주면 지표 정렬 창이 그 스토리지의 최근 N개다 -- 다른 스토리지의
    # 최근 스캔이 창을 채워 이 스토리지의 오래된 큰 디렉터리를 밀어내지 않는다. 다른 스토리지의 잡은 캐시·잡 조회 어디에도
    # 실리지 않는다(스캔도 스토리지별).
    from dms.api import routes_usage
    monkeypatch.setattr(routes_usage, "_WINDOW_LIMIT", 2)
    art = tmp_path / "artifacts"
    client = _client(tmp_path, art)
    _login(client, "admin", "admin")
    s1_jobs = set()
    for i, t in enumerate(["a", "b", "c"]):
        jid, _ = _scan_job(client, storage="s1", target=t, art_base=art, report=_report(mtime_bytes=(10 + i,)))
        _at(client, jid, f"2026-09-0{i + 1}T00:00:00Z")
        s1_jobs.add(jid)
    big, _ = _scan_job(client, storage="s2", target="huge", art_base=art, report=_report(mtime_bytes=(9999,)))
    _at(client, big, "2026-08-01T00:00:00Z")
    small, _ = _scan_job(client, storage="s2", target="tiny", art_base=art, report=_report(mtime_bytes=(1,)))
    _at(client, small, "2026-08-02T00:00:00Z")
    repos = client.app.state.repos
    gets, recents, byids = [], [], []
    _spy(monkeypatch, repos.scan_digests, "get_many", gets)
    _spy(monkeypatch, repos.data_jobs, "recent_scans_for_targets", recents)
    _spy(monkeypatch, repos.data_jobs, "jobs_by_ids", byids)
    rows = client.get("/api/admin/usage/scan-targets", params={"storage": "s2", "sort": "bytes"}).json()
    assert [(r["storage_name"], r["target"]) for r in rows] == [("s2", "huge"), ("s2", "tiny")]
    exp = client.get("/api/admin/usage/export", params={"storage": "s2", "sort": "bytes"}).json()
    assert [r["target"] for r in exp["rows"]] == ["huge", "tiny"] and exp["count"] == 2
    assert {args[0] for args, _ in recents} == {"s2"}
    touched = {j for args, _ in gets + byids for j in args[0]}
    assert touched and not (touched & s1_jobs)
    # 대조: storage 없는 옛 「전체」는 전 스토리지의 최근 2개가 창이라 큰 s2 디렉터리가 창 밖이다(그래서 탭이 storage 를 싣는다)
    rows = client.get("/api/admin/usage/scan-targets", params={"sort": "bytes"}).json()
    assert [(r["storage_name"], r["target"]) for r in rows] == [("s1", "c"), ("s1", "b")]


@pytest.mark.parametrize("sort", ["last_scan", "bytes"])
def test_every_summary_tab_name_scopes_the_list_and_export_to_itself(tmp_path, sort):
    # 탭 이름(/storages)은 그대로 storage 로 되돌아온다 -- 변조·구형 행의 빈 이름도. 예전엔 storage= 를 「필터 없음」으로
    # 접어 빈 이름 탭의 목록·CSV 가 전 스토리지였다(리뷰 2026-10-20: 한 파일 = 한 스토리지가 깨짐).
    art = tmp_path / "artifacts"
    client = _client(tmp_path, art)
    _login(client, "admin", "admin")
    _scan_job(client, storage="s1", target="a", art_base=art)
    _scan_job(client, storage="s2", target="b", art_base=art)
    blank, _ = _scan_job(client, storage="s1", target="c", art_base=art)
    client.app.state.repos.db.execute("UPDATE data_jobs SET storage_name = '' WHERE job_id = :j", {"j": blank})
    names = [s["storage_name"] for s in client.get("/api/admin/usage/storages").json()["storages"]]
    assert "" in names and {"s1", "s2"} <= set(names)
    for name in names:
        q = {"storage": name, "sort": sort}
        rows = client.get("/api/admin/usage/scan-targets", params=q).json()
        exp = client.get("/api/admin/usage/export", params=q).json()
        assert {r["storage_name"] for r in rows} == {name}
        assert {r["storage_name"] for r in exp["rows"]} == {name} and exp["count"] == len(exp["rows"]) == 1
    # storage 를 아예 싣지 않는 옛 탭만 전체다.
    assert len(client.get("/api/admin/usage/scan-targets").json()) == 3


def _drain_export(client, **params):
    """export 라우트를 직접 불러 StreamingResponse 의 본문 조각을 starlette 와 같은 길(스레드풀 반복자)로 하나씩 받는다.
    (parts, 도중 예외)."""
    import asyncio
    from types import SimpleNamespace
    from dms.api import routes_usage
    args = {"q": None, "storage": None, "path": None, "order": "desc", "sort": "last_scan", **params}
    resp = routes_usage.export_usage(SimpleNamespace(app=client.app), **args)
    assert resp.media_type == "application/json"

    async def drain():
        parts = []
        try:
            async for part in resp.body_iterator:
                parts.append(part)
        except Exception as exc:          # noqa: BLE001 -- 도중 실패를 그대로 돌려준다
            return parts, exc
        return parts, None
    return asyncio.run(drain())


@pytest.mark.parametrize("sort", ["last_scan", "hot"])
def test_export_streams_one_part_per_chunk_with_count_last(tmp_path, monkeypatch, sort):
    from dms.api import routes_usage
    monkeypatch.setattr(routes_usage, "_CHUNK", 2)
    art = tmp_path / "artifacts"
    client = _client(tmp_path, art)
    _login(client, "admin", "admin")
    for t in ["a", "b", "c", "d", "e"]:
        _scan_job(client, target=t, art_base=art)
    parts, err = _drain_export(client, sort=sort)
    assert err is None
    assert len(parts) == 1 + 3 + 1                        # 머리(generated_at·truncated) + 묶음 3 + 꼬리(count)
    assert parts[0].startswith(b'{"generated_at":') and parts[0].endswith(b'"truncated":false,"rows":[')
    assert parts[-1] == b'],"count":5}'
    body = json.loads(b"".join(parts))
    assert list(body) == ["generated_at", "truncated", "rows", "count"]
    assert body["count"] == 5 and sorted(r["target"] for r in body["rows"]) == ["a", "b", "c", "d", "e"]
    # HTTP 로도 같은 모양(포탈 apiGet 은 키 순서를 보지 않는다)
    r = client.get("/api/admin/usage/export", params={"sort": sort})
    assert r.headers["content-type"] == "application/json" and r.json()["count"] == 5


def test_export_truncated_is_decided_before_the_first_byte(tmp_path, monkeypatch):
    from dms.api import routes_usage
    monkeypatch.setattr(routes_usage, "_WINDOW_LIMIT", 2)
    monkeypatch.setattr(routes_usage, "_CHUNK", 1)
    art = tmp_path / "artifacts"
    client = _client(tmp_path, art)
    _login(client, "admin", "admin")
    for t in ["a", "b", "c"]:
        _scan_job(client, target=t, art_base=art)
    parts, err = _drain_export(client, sort="bytes")
    assert err is None and b'"truncated":true' in parts[0] and parts[-1] == b'],"count":2}'


def test_export_failure_mid_stream_is_broken_json_not_a_short_file(tmp_path, monkeypatch):
    # 흘리는 도중 실패(DB 끊김 등)는 삼키지 않는다 -- 닫히지 않은 JSON 이라 포탈 파서가 실패로 보인다. 잘 닫힌 짧은 본문이면
    # 「전부」인 척하는 잘린 CSV 가 조용히 내려간다.
    from dms.api import routes_usage
    monkeypatch.setattr(routes_usage, "_CHUNK", 2)
    art = tmp_path / "artifacts"
    client = _client(tmp_path, art)
    _login(client, "admin", "admin")
    for t in ["a", "b", "c", "d", "e"]:
        _scan_job(client, target=t, art_base=art)
    repos = client.app.state.repos
    real = repos.data_jobs.recent_scans_for_targets
    calls = []

    def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("db gone")
        return real(*args, **kwargs)
    monkeypatch.setattr(repos.data_jobs, "recent_scans_for_targets", flaky)
    parts, err = _drain_export(client)
    assert isinstance(err, RuntimeError) and len(parts) == 2          # 머리 + 첫 묶음까지 나갔다
    with pytest.raises(ValueError):
        json.loads(b"".join(parts))
    calls.clear()
    with pytest.raises(RuntimeError):
        client.get("/api/admin/usage/export")


def _bulk_jobs(db, n, *, storage="s1", reports_for=0, art=None):
    """성공 scan 잡 n 개(요청 없이 -- 사용량 질의는 data_jobs 만 본다). 가장 최근 reports_for 개는 리포트를 쓴다."""
    import uuid
    from dms.db import dump_json
    wp = dump_json({"identity": {"username": "alice", "uid": os.getuid(), "gid": os.getgid()}})
    ids = []
    db.execute("PRAGMA synchronous = OFF")
    with db.transaction():
        for i in range(n):
            jid = uuid.uuid4().hex
            at = f"2026-09-01T{i // 3600:02d}:{i // 60 % 60:02d}:{i % 60:02d}Z"
            db.execute("""INSERT INTO data_jobs (job_id, request_id, operation, tool, storage_name, target, options,
                                                 priority, state, worker_pool, bytes_count, created_at, updated_at)
                          VALUES (:j, :r, 'scan', 'dscan', :sn, :t, '{}', 'mid', 'Succeeded', :wp, :b, :at, :at)""",
                       {"j": jid, "r": uuid.uuid4().hex, "sn": storage, "t": f"t{i:05d}", "wp": wp, "b": i, "at": at})
            ids.append(jid)
    for jid in ids[len(ids) - reports_for:]:
        d = art / jid / "execution"
        d.mkdir(parents=True)
        (d / "dscan-report.json").write_text(json.dumps(_report()))
    return ids


def test_real_chunk_bounds_every_id_list_of_the_window_and_the_stream(tmp_path, monkeypatch):
    # 실제 상수(500)로: 1,100 타깃 창에서 캐시 조회(get_many)·잡 조회(target IN·job_id IN)·캐시 쓰기(put_many)가 한 번에
    # 500 개를 넘게 받지 않는다 -- 호출 단위와 문장 단위(자리표시자 수) 둘 다. 캐시가 빈 리포트 520 개는 묶음마다 나뉘어 들어간다.
    import re
    art = tmp_path / "artifacts"
    client = _client(tmp_path, art)
    _login(client, "admin", "admin")
    repos = client.app.state.repos
    _bulk_jobs(repos.db, 1100, reports_for=520, art=art)
    calls = {"get_many": [], "recent_scans_for_targets": [], "jobs_by_ids": [], "put_many": []}
    _spy(monkeypatch, repos.scan_digests, "get_many", calls["get_many"])
    _spy(monkeypatch, repos.scan_digests, "put_many", calls["put_many"])
    _spy(monkeypatch, repos.data_jobs, "recent_scans_for_targets", calls["recent_scans_for_targets"])
    _spy(monkeypatch, repos.data_jobs, "jobs_by_ids", calls["jobs_by_ids"])
    widest = []
    real_query, real_execute = repos.db.query, repos.db.execute

    def width(sql):
        return max([len(set(re.findall(rf":{p}\d+", sql))) for p in ("j", "t", "d")])

    def query(sql, params=None):
        widest.append(width(sql))
        return real_query(sql, params)

    def execute(sql, params=None):
        widest.append(width(sql))
        return real_execute(sql, params)
    monkeypatch.setattr(repos.db, "query", query)
    monkeypatch.setattr(repos.db, "execute", execute)

    body = client.get("/api/admin/usage/export", params={"sort": "bytes", "storage": "s1"}).json()
    assert body["count"] == 1100 and body["truncated"] is False
    assert len({r["target"] for r in body["rows"]}) == 1100
    assert real_query("SELECT COUNT(*) AS n FROM scan_report_digests")[0]["n"] == 520
    rows = client.get("/api/admin/usage/scan-targets", params={"sort": "hot", "limit": 200}).json()
    assert len(rows) == 200
    assert client.get("/api/admin/usage/export", params={"storage": "s1"}).json()["count"] == 1100
    # 스토리지 합계(2026-10-21)도 같은 묶음 상한 안이다 -- 최상위 1,100 개 중 캐시에 없는 580 개는 읽기 예산(500)만큼만
    # 열고(아티팩트가 없어 전부 못 읽음 -- 영구 캐시에 넣지 않고 실패 기억에) 나머지는 pending 이다. 실패를 기억했으니
    # 진전이다(다음 호출이 나머지 80 개를 연다).
    tot = client.get("/api/admin/usage/storage-totals", params={"storage": "s1"}).json()
    assert (tot["target_count"], tot["summed_target_count"]) == (1100, 1100)
    assert (tot["unreadable_count"], tot["pending_count"], tot["progressed"]) == (500, 80, True)
    assert tot["total_files"] == {"sum": 520 * 7, "known": 520, "unknown": 580}
    assert tot["total_bytes"]["known"] == 1100                 # 못 읽은·지연 대상도 실 사용량은 bytes_count(목록 열 규칙)

    sizes = {name: [len(list(c[0][1] if name in ("recent_scans_for_targets",) else c[0][0])) for c in cs]
             for name, cs in calls.items()}
    assert sizes["get_many"] and max(sizes["get_many"]) == 500
    assert max(sizes["recent_scans_for_targets"]) == 500
    assert max(sizes["jobs_by_ids"]) == 500 and max(sizes["put_many"]) == 500
    assert sum(sizes["put_many"]) == 520                    # 캐시가 빈 리포트는 한 번만 읽혀 들어간다
    assert max(widest) == 500


def test_storages_summary_lists_registered_then_unregistered_with_counts(tmp_path):
    # 탭 = 등록 ∪ 성공 scan 기록이 있는 이름. 등록인데 기록이 없으면 0·null(0 은 정상값 -- 탭이 「0」으로 선다). 미등록은
    # 감사 기록의 마지막 설정(없으면 모름). 실패 scan 만 남은 이름은 사용량 기준(성공 scan) 밖이다.
    art = tmp_path / "artifacts"
    client = _client(tmp_path, art)
    _login(client, "admin", "admin")
    repos = client.app.state.repos
    for name, backend in (("zeta", "gpfs"), ("alpha", "cephfs"), ("gone", "wekafs")):
        repos.storages.create(storage_name=name, mount_path=f"/{name}", managed_root=f"/{name}/m",
                              backend_type=backend, actor="t")
    repos.storages.delete("gone", actor="t")
    j1, _ = _scan_job(client, storage="alpha", target="a", art_base=art)
    j2, _ = _scan_job(client, storage="alpha", target="a", art_base=art)
    j3, _ = _scan_job(client, storage="alpha", target="b", art_base=art)
    _at(client, j1, "2026-09-01T00:00:00Z")
    _at(client, j2, "2026-09-03T00:00:00Z")
    _at(client, j3, "2026-09-02T00:00:00Z")
    _scan_job(client, storage="gone", target="x", art_base=art)
    _scan_job(client, storage="bygone", target="y", art_base=art)
    failed, _ = _scan_job(client, storage="failonly", target="z", art_base=art)
    repos.db.execute("UPDATE data_jobs SET state = 'Failed' WHERE job_id = :j", {"j": failed})
    body = client.get("/api/admin/usage/storages").json()
    assert body["window_limit"] == 10000
    st = body["storages"]
    assert [s["storage_name"] for s in st] == ["alpha", "zeta", "bygone", "gone"]
    assert st[0] == {"storage_name": "alpha", "registered": True, "source": "registered", "backend_type": "cephfs",
                     "mount_path": "/alpha", "target_count": 2, "succeeded_scan_count": 3,
                     "last_scan_at": "2026-09-03T00:00:00Z"}
    assert st[1] == {"storage_name": "zeta", "registered": True, "source": "registered", "backend_type": "gpfs",
                     "mount_path": "/zeta", "target_count": 0, "succeeded_scan_count": 0, "last_scan_at": None}
    assert (st[2]["registered"], st[2]["source"], st[2]["backend_type"], st[2]["mount_path"]) == (False, None, None, None)
    assert (st[3]["registered"], st[3]["source"], st[3]["backend_type"], st[3]["mount_path"]) == (
        False, "last_known", "wekafs", "/gone")
    assert (st[3]["target_count"], st[3]["succeeded_scan_count"]) == (1, 1)
    # 옛 탭 호환: scan-storages 의 모양(string[])은 그대로다
    assert client.get("/api/admin/usage/scan-storages").json() == ["alpha", "bygone", "gone"]


def test_storages_summary_requires_admin(tmp_path):
    client = _client(tmp_path, tmp_path / "artifacts")
    _login(client, "user1", "user")
    assert client.get("/api/admin/usage/storages").status_code == 403


# ---- 2026-10-21: 스토리지 합계(GET /api/admin/usage/storage-totals) ----

TOTALS = "/api/admin/usage/storage-totals"
_TOTAL_KEYS = ["storage_name", "computed_at", "truncated", "window_limit", "target_count", "summed_target_count",
               "nested_target_count", "invalid_target_count", "unreadable_count", "pending_count", "progressed",
               "total_bytes", "total_files", "total_directories", "total_symlinks", "total_other", "total_entries",
               "scan_errors", "broken_paths_total", "hot_ratio", "file_size_histogram", "oldest_scan_at",
               "newest_scan_at", "filesystem"]


def _totals(client, storage="s1"):
    r = client.get(TOTALS, params={"storage": storage})
    assert r.status_code == 200, r.text
    body = r.json()
    assert list(body) == _TOTAL_KEYS                           # 계약 모양(포탈 타입과 한 벌)
    return body


def test_storage_totals_sum_only_maximal_targets_with_their_latest_scan(tmp_path):
    # 대상 경로가 겹치면(team ⊃ team/sub1) 단순 합은 하위를 두 번 센다 -- 최상위 대상의 최신 scan 만 더한다. 'team-x' 는
    # 'team' 의 하위가 아니다(문자열 접두가 아니라 경로 성분). 하위가 상위보다 새로 스캔돼도 상위 값이 들어가고, 시점 범위에
    # 하위의 시각은 들어가지 않는다. 하위 대상의 리포트는 열지도 않는다.
    art = tmp_path / "artifacts"
    client = _client(tmp_path, art)
    _login(client, "admin", "admin")
    spec = [("team", "2026-09-01", dict(total=10, files=1, hot_bytes=10, cold_bytes=0)),
            ("team", "2026-09-20", dict(total=50, files=7, hot_bytes=50, cold_bytes=0)),
            ("team/sub1", "2026-10-01", dict(total=5, files=2, hot_bytes=5, cold_bytes=0)),
            ("team-x", "2026-09-10", dict(total=100, files=4, hot_bytes=100, cold_bytes=0)),
            ("other", "2026-08-16", dict(total=1000, files=3, hot_bytes=0, cold_bytes=1000))]
    jobs = {}
    for target, day, m in spec:
        jid, _ = _scan_job(client, target=target, art_base=art, report=_metric_report(**m))
        _at(client, jid, f"{day}T00:00:00Z")
        jobs[target] = jid
    _scan_job(client, storage="s2", target="elsewhere", art_base=art)         # 다른 스토리지는 섞이지 않는다
    t = _totals(client)
    assert t["storage_name"] == "s1" and t["truncated"] is False and t["window_limit"] == 10000
    assert (t["target_count"], t["summed_target_count"], t["nested_target_count"], t["invalid_target_count"]) == (
        4, 3, 1, 0)
    assert (t["unreadable_count"], t["pending_count"], t["progressed"]) == (0, 0, True)
    assert t["total_bytes"] == {"sum": 50 + 100 + 1000, "known": 3, "unknown": 0}
    assert t["total_files"] == {"sum": 14, "known": 3, "unknown": 0}
    assert t["total_directories"] == {"sum": 9, "known": 3, "unknown": 0}
    assert t["total_entries"] == {"sum": 30, "known": 3, "unknown": 0}
    assert t["total_symlinks"] == t["total_other"] == t["scan_errors"] == t["broken_paths_total"] == {
        "sum": 0, "known": 3, "unknown": 0}                                    # 0 은 값이다
    # hot 은 bytes 가중: (50 + 100) / 1150 -- 비율의 평균((1 + 1 + 0) / 3)이 아니다
    assert t["hot_ratio"] == {"value": pytest.approx(150 / 1150), "hot_bytes": 150, "total_bytes": 1150,
                              "known": 3, "unknown": 0}
    assert t["file_size_histogram"] == {"buckets": [{"bucket": "[0,4096]", "lower_inclusive": 0,
                                                     "upper_inclusive": 4096, "count": 14}], "known": 3, "unknown": 0}
    assert (t["oldest_scan_at"], t["newest_scan_at"]) == ("2026-08-16T00:00:00Z", "2026-09-20T00:00:00Z")
    assert t["filesystem"] is None                             # 미등록 이름(에이전트 보고도 없다)
    cached = {r["job_id"] for r in _digest_rows(client)}
    assert cached == {jobs["team"], jobs["team-x"], jobs["other"]}            # 하위(team/sub1)는 열지 않았다
    again = _totals(client)
    assert again["progressed"] is False and again["total_bytes"] == t["total_bytes"]


def _fake_failure_clock(client):
    """합계의 읽기 실패 기억(app.state.report_read_failures)을 손으로 돌리는 시계로 바꾼다 -- [now] 를 돌려준다."""
    from dms.api.routes_usage import ReportReadFailures
    now = [1000.0]
    client.app.state.report_read_failures = ReportReadFailures(clock=lambda: now[0])
    return now


def test_storage_totals_unreadable_top_target_is_unknown_but_bytes_fall_back_to_db(tmp_path):
    from dms.api import routes_usage
    # 못 읽은 최상위 대상: 리포트 유래 값은 모름(unknown), 실 사용량만 러너가 남긴 bytes_count(목록 열과 같은 대체).
    # 영구 캐시에 넣지 않는다 -- 리포트가 나중에 생기면 실패 기억(TTL)이 지난 뒤의 호출이 읽는다. 하위 대상으로 대체하지 않는다.
    art = tmp_path / "artifacts"
    client = _client(tmp_path, art)
    _login(client, "admin", "admin")
    jid, _ = _scan_job(client, target="team", art_base=art, write_report=False)
    client.app.state.repos.data_jobs.set_artifact(jid, artifact_uri="file:///x",
                                                  result_summary={"files": 10, "bytes": 1234})
    sub, _ = _scan_job(client, target="team/sub", art_base=art)
    _at(client, jid, "2026-09-01T00:00:00Z")
    _at(client, sub, "2026-09-02T00:00:00Z")
    clock = _fake_failure_clock(client)
    t = _totals(client)
    assert (t["summed_target_count"], t["nested_target_count"]) == (1, 1)
    assert (t["unreadable_count"], t["pending_count"], t["progressed"]) == (1, 0, True)    # 실패를 기억했다(진전)
    assert t["total_bytes"] == {"sum": 1234, "known": 1, "unknown": 0}
    assert t["total_files"] == {"sum": None, "known": 0, "unknown": 1}        # 하위 값(7)으로 메우지 않는다
    assert t["hot_ratio"]["value"] is None and t["hot_ratio"]["unknown"] == 1
    assert t["file_size_histogram"] == {"buckets": [], "known": 0, "unknown": 1}
    assert _digest_rows(client) == []
    d = art / jid / "execution"
    d.mkdir(parents=True)
    (d / "dscan-report.json").write_text(json.dumps(_report()))
    t = _totals(client)
    assert (t["unreadable_count"], t["progressed"]) == (1, False)            # TTL 안: 다시 열지 않는다
    clock[0] += routes_usage._FAILED_READ_TTL_SECONDS
    t = _totals(client)
    assert (t["unreadable_count"], t["progressed"]) == (0, True)
    assert t["total_bytes"] == {"sum": 50, "known": 1, "unknown": 0}          # 리포트를 읽으면 목록 열처럼 리포트 값
    assert t["total_files"] == {"sum": 7, "known": 1, "unknown": 0}


def test_storage_totals_read_budget_leaves_pending_and_next_call_progresses(tmp_path, monkeypatch):
    # 한 호출이 여는 아티팩트는 예산만큼 -- 나머지는 pending(리포트 모름)이고 다음 호출이 캐시가 찬 만큼 이어 간다.
    # 포탈은 pending > 0 && progressed 동안 다시 부른다 -- 다 읽은 뒤의 호출은 progressed False 다(멈춤 신호).
    from dms.api import routes_usage
    monkeypatch.setattr(routes_usage, "_TOTALS_READ_BUDGET", 1)
    art = tmp_path / "artifacts"
    client = _client(tmp_path, art)
    _login(client, "admin", "admin")
    for i, t in enumerate(["a", "b", "c"]):
        jid, _ = _scan_job(client, target=t, art_base=art)
        _at(client, jid, f"2026-09-0{i + 1}T00:00:00Z")
    seen = []
    for _ in range(4):
        t = _totals(client)
        seen.append((t["pending_count"], t["progressed"], t["total_files"]["known"], t["total_files"]["unknown"]))
    assert seen == [(2, True, 1, 2), (1, True, 2, 1), (0, True, 3, 0), (0, False, 3, 0)]
    assert t["unreadable_count"] == 0 and t["total_files"]["sum"] == 21


def test_storage_totals_unreadable_heads_do_not_starve_readable_targets_behind_them(tmp_path, monkeypatch):
    # 2026-10-21 리뷰: 못 읽은 리포트는 영구 캐시에 두지 않으므로, 기억이 없으면 같은 실패가 호출마다 같은 순서(최근 스캔
    # 순)로 예산 앞자리를 먼저 쓴다 -- 영구히 못 읽는 최상위가 예산만큼 앞에 있으면 뒤의 읽을 수 있는 대상은 영영 pending
    # 이고 progressed False 라 포탈이 멈췄다(옛 동작: 매 호출 (pending 4, False, known 0)). 실패 기억(TTL) 안의 잡은 열지
    # 않고 unreadable 로 세며 예산은 아직 안 열어 본 잡이 먼저 -- 실패만 기억해도 진전이다(다음 호출이 그 뒤를 연다).
    from dms.api import routes_usage
    monkeypatch.setattr(routes_usage, "_TOTALS_READ_BUDGET", 2)
    opened = []
    real_read = routes_usage._read_digest
    monkeypatch.setattr(routes_usage, "_read_digest", lambda base, job: (opened[-1].append(job["target"]),
                                                                        real_read(base, job))[1])
    art = tmp_path / "artifacts"
    client = _client(tmp_path, art)
    _login(client, "admin", "admin")
    clock = _fake_failure_clock(client)
    jobs = {}
    # 최근 스캔 순 u1, u2, r1, u3, r2, r3 -- u = 리포트 없음(영구히 못 읽음), r = 읽을 수 있음
    for day, target in [(6, "u1"), (5, "u2"), (4, "r1"), (3, "u3"), (2, "r2"), (1, "r3")]:
        jid, _ = _scan_job(client, target=target, art_base=art, write_report=target.startswith("r"))
        _at(client, jid, f"2026-09-0{day}T00:00:00Z")
        jobs[target] = jid

    def drain(n):
        seen = []
        for _ in range(n):
            opened.append([])
            t = _totals(client)
            seen.append((t["pending_count"], t["progressed"], t["total_files"]["known"], t["unreadable_count"]))
        return seen
    assert drain(4) == [(4, True, 0, 2), (2, True, 1, 3), (0, True, 3, 3), (0, False, 3, 3)]
    # TTL 안의 실패는 다시 열지 않는다(한 호출 안의 여는 순서는 jobs_by_ids 사전 순이라 집합으로 본다)
    assert [set(o) for o in opened] == [{"u1", "u2"}, {"r1", "u3"}, {"r2", "r3"}, set()]
    assert _totals(client)["total_files"] == {"sum": 21, "known": 3, "unknown": 3}
    assert {r["job_id"] for r in _digest_rows(client)} == {jobs["r1"], jobs["r2"], jobs["r3"]}   # 실패는 영구 캐시 밖

    # TTL 이 지나면 다시 연다(일시 오류를 영구 「모름」으로 굳히지 않는다) -- 단 아직 열어 본 적 없는 잡(가장 오래된 r4)이
    # 예산을 먼저 받고, 옛 실패는 최근 스캔 순으로 그 뒤다. 그 사이 리포트가 생긴 u1 은 읽힌다.
    r4, _ = _scan_job(client, target="r4", art_base=art)
    _at(client, r4, "2026-08-15T00:00:00Z")
    d = art / jobs["u1"] / "execution"
    d.mkdir(parents=True)
    (d / "dscan-report.json").write_text(json.dumps(_report()))
    clock[0] += routes_usage._FAILED_READ_TTL_SECONDS
    opened.clear()
    assert drain(3) == [(2, True, 5, 0), (0, True, 5, 2), (0, False, 5, 2)]
    assert [set(o) for o in opened] == [{"r4", "u1"}, {"u2", "u3"}, set()]


def test_report_read_failures_ttl_and_cap_never_evict_live_entries():
    # 상한이 차면 만료 항목만 걷고, 그래도 차 있으면 새 실패를 기록하지 않는다(살아 있는 기억을 밀어내면 여러 스토리지의
    # 실패가 서로를 밀어내며 포탈이 같은 실패를 1초마다 끝없이 다시 부를 수 있다) -- record 는 실제로 기록한 수를 돌려준다.
    from dms.api.routes_usage import ReportReadFailures
    now = [0.0]
    f = ReportReadFailures(ttl_seconds=10, cap=2, clock=lambda: now[0])
    assert f.record(["a", "b", "c"]) == 2
    assert f.split(["a", "b", "c"]) == ({"a", "b"}, set())
    now[0] = 5
    assert f.record(["a"]) == 1                                # 이미 있는 id 는 상한과 무관하게 시각만 새로
    now[0] = 12
    assert f.split(["a", "b", "c"]) == ({"a"}, {"b"})          # b 는 만료(옛 실패 -- 재시도 대상), c 는 기억에 없음
    assert f.record(["c", "d"]) == 1                           # 만료된 b 를 걷고 c 는 들어가고, d 는 자리가 없다
    assert f.split(["a", "b", "c", "d"]) == ({"a", "c"}, set())


def test_storage_totals_unrecorded_failure_is_not_progress(tmp_path):
    # 실패 기억이 상한이라 기록하지 못한 실패는 진전이 아니다 -- progressed True 로 말하면 포탈이 1초마다 같은 실패를
    # 다시 연다(옛 동작으로 돌아갈 뿐, 끝없는 재조회는 없다).
    from dms.api.routes_usage import ReportReadFailures
    art = tmp_path / "artifacts"
    client = _client(tmp_path, art)
    _login(client, "admin", "admin")
    full = ReportReadFailures(cap=1)
    assert full.record(["someone-else"]) == 1
    client.app.state.report_read_failures = full
    _scan_job(client, target="team", art_base=art, write_report=False)
    t = _totals(client)
    assert (t["unreadable_count"], t["pending_count"], t["progressed"]) == (1, 0, False)


def test_storage_totals_empty_and_unknown_storage_is_null_not_zero(tmp_path):
    # 기록이 없는 스토리지(등록만 됨)·없는 이름은 404 가 아니라 빈 합계 -- 모든 sum 은 null(0 B 가 아니다)
    client = _client(tmp_path, tmp_path / "artifacts")
    _login(client, "admin", "admin")
    client.app.state.repos.storages.create(storage_name="alpha", mount_path="/alpha", managed_root="/alpha/m",
                                           backend_type="cephfs", actor="t")
    for name in ("alpha", "nope"):
        t = _totals(client, name)
        assert t["storage_name"] == name
        assert (t["target_count"], t["summed_target_count"], t["nested_target_count"], t["invalid_target_count"],
                t["unreadable_count"], t["pending_count"], t["progressed"], t["truncated"]) == (
            0, 0, 0, 0, 0, 0, False, False)
        for k in ("total_bytes", "total_files", "total_directories", "total_symlinks", "total_other", "total_entries",
                  "scan_errors", "broken_paths_total"):
            assert t[k] == {"sum": None, "known": 0, "unknown": 0}, k
        assert t["hot_ratio"] == {"value": None, "hot_bytes": None, "total_bytes": None, "known": 0, "unknown": 0}
        assert t["file_size_histogram"] == {"buckets": [], "known": 0, "unknown": 0}
        assert t["oldest_scan_at"] is None and t["newest_scan_at"] is None and t["filesystem"] is None


def test_storage_totals_empty_name_is_its_own_storage(tmp_path):
    # 실린 storage 는 빈 문자열도 그 이름 하나다(scan-targets 와 같은 정확 일치 -- /storages 가 빈 이름도 탭으로 낸다).
    # 「필터 없음」으로 접으면 빈 이름 탭의 합계가 전 스토리지가 된다. storage 자체가 없으면 422(필수).
    art = tmp_path / "artifacts"
    client = _client(tmp_path, art)
    _login(client, "admin", "admin")
    _scan_job(client, storage="s1", target="a", art_base=art, report=_report(mtime_bytes=(10,)))
    _scan_job(client, storage="s2", target="b", art_base=art, report=_report(mtime_bytes=(20,)))
    blank, _ = _scan_job(client, storage="s1", target="c", art_base=art, report=_report(mtime_bytes=(40,)))
    client.app.state.repos.db.execute("UPDATE data_jobs SET storage_name = '' WHERE job_id = :j", {"j": blank})
    t = _totals(client, "")
    assert t["storage_name"] == "" and t["target_count"] == 1
    assert t["total_bytes"] == {"sum": 40, "known": 1, "unknown": 0}
    assert _totals(client, "s1")["total_bytes"]["sum"] == 10
    assert client.get(TOTALS).status_code == 422


def test_storage_totals_requires_admin(tmp_path):
    client = _client(tmp_path, tmp_path / "artifacts")
    _login(client, "user1", "user")
    assert client.get(TOTALS, params={"storage": "s1"}).status_code == 403


def test_storage_totals_truncation_is_told(tmp_path, monkeypatch):
    # 창(최근 스캔 순 _WINDOW_LIMIT)을 넘으면 부분 합이라고 말한다(조용한 절단 금지) -- 창 안의 대상만 센다.
    from dms.api import routes_usage
    monkeypatch.setattr(routes_usage, "_WINDOW_LIMIT", 2)
    art = tmp_path / "artifacts"
    client = _client(tmp_path, art)
    _login(client, "admin", "admin")
    for i, t in enumerate(["t1", "t2", "t3"]):
        jid, _ = _scan_job(client, target=t, art_base=art)
        _at(client, jid, f"2026-09-0{i + 1}T00:00:00Z")
    t = _totals(client)
    assert (t["truncated"], t["window_limit"], t["target_count"], t["summed_target_count"]) == (True, 2, 2, 2)
    assert t["oldest_scan_at"] == "2026-09-02T00:00:00Z"              # 창 밖(t1)은 시점 범위에도 없다
    monkeypatch.setattr(routes_usage, "_WINDOW_LIMIT", 3)
    assert _totals(client)["truncated"] is False


def test_storage_totals_tampered_target_rows_are_counted_not_summed(tmp_path):
    # DB 가 신뢰 경계(create_job 은 무검증 INSERT): 모양이 틀린 target 은 invalid 로 세고 합에도 조상 판정에도 쓰지 않는다 --
    # 'team/' 를 조상으로 믿으면 'team/x' 가 빠지고, '' 를 조상으로 믿으면 스토리지 전체가 빠진다.
    art = tmp_path / "artifacts"
    client = _client(tmp_path, art)
    _login(client, "admin", "admin")
    for target in ("team/", "", "a/../b"):
        _scan_job(client, target=target, art_base=art, report=_report(mtime_bytes=(999,)))
    _scan_job(client, target="team/x", art_base=art, report=_report(mtime_bytes=(10,)))
    t = _totals(client)
    assert (t["target_count"], t["summed_target_count"], t["invalid_target_count"], t["nested_target_count"]) == (
        4, 1, 3, 0)
    assert t["total_bytes"] == {"sum": 10, "known": 1, "unknown": 0}


def _agent_report(storage, *, status, is_mountpoint, total, used):
    return {"mounts": [{"storage_name": storage, "mount_path": f"/{storage}", "exists": True,
                        "is_mountpoint": is_mountpoint, "readable": True, "writable": True, "status": status,
                        "reason": None if status == "Ready" else "not_a_mountpoint"}],
            "os": {"disks": [{"storage_name": storage, "total_bytes": total, "used_bytes": used}]}}


def test_storage_totals_filesystem_from_agent_reports(tmp_path):
    # df 는 그 스토리지가 Ready 마운트포인트인 노드의 statvfs 만 -- 마운트가 없는 노드가 실은 노드 루트 디스크 값(실측:
    # 노드마다 다른 40 GB)은 버린다. 미등록 이름은 옛 보고가 Ready 라도 null(지금 값이 아니다).
    art = tmp_path / "artifacts"
    client = _client(tmp_path, art)
    _login(client, "admin", "admin")
    repos = client.app.state.repos
    repos.storages.create(storage_name="alpha", mount_path="/alpha", managed_root="/alpha/m",
                          backend_type="cephfs", actor="t")
    repos.agents.ingest("w1", _agent_report("alpha", status="Missing", is_mountpoint=False,
                                            total=40483942400, used=15265992704))
    repos.agents.ingest("w4", _agent_report("alpha", status="Ready", is_mountpoint=True,
                                            total=24498929664, used=37748736))
    repos.agents.ingest("w9", _agent_report("gone", status="Ready", is_mountpoint=True, total=100, used=1))
    _scan_job(client, storage="alpha", target="a", art_base=art)
    _scan_job(client, storage="gone", target="b", art_base=art)
    fs = _totals(client, "alpha")["filesystem"]
    assert {k: fs[k] for k in ("total_bytes", "used_bytes", "node_name", "fresh", "node_count", "consistent")} == {
        "total_bytes": 24498929664, "used_bytes": 37748736, "node_name": "w4", "fresh": True, "node_count": 1,
        "consistent": True}
    assert isinstance(fs["reported_at"], str)
    assert _totals(client, "gone")["filesystem"] is None
