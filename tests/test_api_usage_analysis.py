"""사용량 분석 라우트(routes_usage.py) — scan 이력 시계열·타깃 검색.

픽스처 관례는 test_api_request_scan_stats 를 따른다(성공 잡 materialize +
execution/dscan-report.json 직접 기록)."""
import os
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
    monkeypatch.setattr(routes_usage, "_EXPORT_LIMIT", 1)
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


def test_export_reads_jobs_of_selected_targets_in_chunks(tmp_path, monkeypatch):
    # 수천 타깃 내보내기는 (storage, target) 쌍을 묶음으로 나눠 묻는다 -- 묶음 경계에서 빠지는 타깃이 없어야 한다
    from dms.repositories import data_jobs
    monkeypatch.setattr(data_jobs, "_PAIR_CHUNK", 2)
    art = tmp_path / "artifacts"
    client = _client(tmp_path, art)
    _login(client, "admin", "admin")
    for t in ["a", "b", "c", "d", "e"]:
        _scan_job(client, target=t, art_base=art)
    rows = client.get("/api/admin/usage/export").json()["rows"]
    assert len(rows) == 5 and all(r["latest"]["total_bytes"] == 50 for r in rows)


def test_scan_storages_lists_names_with_scan_history(tmp_path):
    # 필터 선택지: 등록이 지워진 스토리지 이름으로 남은 이력도 좁힐 수 있어야 한다(화면이 등록 목록과 합친다)
    art = tmp_path / "artifacts"
    client = _client(tmp_path, art)
    _login(client, "admin", "admin")
    _scan_job(client, storage="s2", target="a", art_base=art)
    _scan_job(client, storage="gone", target="b", art_base=art)
    assert client.get("/api/admin/usage/scan-storages").json() == ["gone", "s2"]
