"""사용량 분석 라우트(routes_usage.py) — scan 이력 시계열·타깃 검색.

픽스처 관례는 test_api_request_scan_stats 를 따른다(성공 잡 materialize +
execution/dscan-report.json 직접 기록)."""
import copy
import os
import json
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from dms.agent.k8s_fetch import project_pv, project_pvc, project_role_binding, project_storage_class
from dms.config import Settings
from dms.db import Database
from dms.domain import DataJobState
from dms.k8s_sync import normalize_snapshot
from dms.migrations import migrate
# 필터 절(2026-10-29)의 테스트베드 모양 세계 -- dmsk8s 실데이터 동기화 + 스토리지 4종 + SC 매핑 + 계정(test_k8s_submit 의 것).
from test_k8s_submit import _FILES, FIXTURES, _k8s_settings, _sync_cluster, world  # noqa: F401


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
               "scan_errors", "invalid_name_entries", "broken_paths_total", "hot_ratio", "file_size_histogram",
               "oldest_scan_at",
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


# ---- 2026-10-26: dscan 포크 ae781bd 의 summary.total_bytes·summary.invalid_name_entries ----

def _new_report(*, total_bytes, invalid, mtime_bytes=(30, 20), files=7):
    r = _report(mtime_bytes=mtime_bytes, atime_bytes=mtime_bytes, files=files)
    r["summary"].update({"total_bytes": total_bytes, "invalid_name_entries": invalid})
    return r


def test_new_counters_flow_through_list_history_export_and_cache(tmp_path):
    """새 리포트: 실 사용량 = summary.total_bytes(히스토그램 합보다 우선), invalid_name_entries 는 포인트의 숫자(카운터만 --
    broken_paths_total 에 더하지 않는다). 구형 리포트(키 부재): 실 사용량은 히스토그램 합 그대로, 카운터는 None(0 이 아니다).
    값은 캐시(scan_report_digests)를 거친 두 번째 조회에서도 같다(재투영 멱등)."""
    art = tmp_path / "artifacts"
    client = _client(tmp_path, art)
    _login(client, "admin", "admin")
    old, _ = _scan_job(client, target="team", art_base=art, report=_report(mtime_bytes=(10,), atime_bytes=(10,)))
    new, _ = _scan_job(client, target="team", art_base=art, report=_new_report(total_bytes=1074750158, invalid=15))
    legacy, _ = _scan_job(client, target="legacy", art_base=art)
    _at(client, old, "2026-09-01T00:00:00Z")
    _at(client, new, "2026-09-20T00:00:00Z")
    _at(client, legacy, "2026-08-01T00:00:00Z")
    for _ in range(2):                                             # 빈 캐시(리포트 투영) → 데운 캐시(재투영)
        rows = {r["target"]: r for r in client.get("/api/admin/usage/scan-targets").json()}
        assert rows["team"]["latest"]["total_bytes"] == 1074750158
        assert rows["team"]["latest"]["invalid_name_entries"] == 15
        assert rows["team"]["latest"]["broken_paths_total"] == 0
        assert rows["legacy"]["latest"]["total_bytes"] == 50
        assert rows["legacy"]["latest"]["invalid_name_entries"] is None
        hist = client.get("/api/admin/usage/scan-history", params={"storage": "s1", "target": "team"}).json()
        assert [(p["total_bytes"], p["invalid_name_entries"]) for p in hist["points"]] == [(10, None), (1074750158, 15)]
        exp = {r["target"]: r for r in client.get("/api/admin/usage/export").json()["rows"]}
        assert exp["team"]["latest"]["invalid_name_entries"] == 15
        assert exp["team"]["previous"]["total_bytes"] == 10
    # 지표 정렬(실 사용량)도 같은 값으로 정렬한다
    ranked = client.get("/api/admin/usage/scan-targets", params={"sort": "bytes", "order": "desc"}).json()
    assert [r["target"] for r in ranked] == ["team", "legacy"]


def test_new_counters_fail_soft_and_old_cache_rows_reproject(tmp_path):
    """DB 가 신뢰 경계: 캐시 행의 최상위 키는 믿지 않고 summary 에서 다시 뽑는다(옛 캐시 행엔 최상위 키가 없다). 수상한
    값(음수·실수·bool·문자열)은 None(카운터) / 히스토그램 합 물러남(실 사용량)."""
    art = tmp_path / "artifacts"
    client = _client(tmp_path, art)
    _login(client, "admin", "admin")
    repos = client.app.state.repos
    cases = {"neg": (-1, -1), "float": (1.5, 2.0), "bool": (True, True), "str": ("9", "9")}
    for name, (tb, inv) in cases.items():
        jid, _ = _scan_job(client, target=name, art_base=art, write_report=False)
        repos.scan_digests.put(jid, {
            "summary": {"total_files": 1, "total_bytes": tb, "invalid_name_entries": inv},
            "time_histograms": {"mtime": [{"bucket": "[0d,1d]", "bytes": 77}]},
            "invalid_name_entries": 99, "total_bytes": 12345})       # 최상위 키는 무시된다
    plain, _ = _scan_job(client, target="oldcache", art_base=art, write_report=False)
    repos.scan_digests.put(plain, {"summary": {"total_files": 1, "total_bytes": 4096, "invalid_name_entries": 3},
                                   "time_histograms": {}})
    rows = {r["target"]: r["latest"] for r in client.get("/api/admin/usage/scan-targets").json()}
    for name in cases:
        assert rows[name]["total_bytes"] == 77, name                # 히스토그램 합으로 물러난다
        assert rows[name]["invalid_name_entries"] is None, name
    assert rows["oldcache"]["total_bytes"] == 4096 and rows["oldcache"]["invalid_name_entries"] == 3


def test_storage_totals_sum_invalid_names_separately_from_broken(tmp_path):
    """스토리지 합계의 invalid_name_entries: 아는 대상만 더하고 구형 리포트는 unknown(0 으로 접지 않는다). broken 합에는
    들지 않는다. 실 사용량 합은 summary.total_bytes 를 쓴다."""
    art = tmp_path / "artifacts"
    client = _client(tmp_path, art)
    _login(client, "admin", "admin")
    _scan_job(client, target="a", art_base=art, report=_new_report(total_bytes=1000, invalid=4))
    _scan_job(client, target="b", art_base=art, report=_new_report(total_bytes=24, invalid=0))
    _scan_job(client, target="c", art_base=art)                               # 구형 리포트
    t = _totals(client)
    assert t["invalid_name_entries"] == {"sum": 4, "known": 2, "unknown": 1}
    assert t["broken_paths_total"] == {"sum": 0, "known": 3, "unknown": 0}
    assert t["total_bytes"] == {"sum": 1000 + 24 + 50, "known": 3, "unknown": 0}


# ---- 2026-10-29: 필터(사용자별·PV 이름·namespace 등) -- /targets·/targets/export·/facets·필터 합계 ----
# 테스트베드 모양: k8s-pv-nfs 가 두 클러스터의 PV 를 함께 담는다(dmsk8s 의 tb-nfs 프리셋 + dmsk8s2 의 c2-nfs 템플릿
# c2/{namespace}/{pvc}), gpfs 의 PVC 이름 models 가 team-a·team-b 둘 다, onefs 의 한 대상을 요청자 둘(alice → junwoo.song)이
# PV 주소로 scan, 면제 운영자(mason)의 직접 경로 scan(PV 밖 둘·PV 루트의 상위 하나·PV 루트 아래 하나), 지운 PVC(team-d/old)의
# PV 주소 scan 기록 하나. DB 는 conftest 의 db 다(DMS_TEST_DB=postgresql 이면 PG 스키마에서도 돈다).

NFS, ONEFS, GPFS, CEPH = "k8s-pv-nfs", "k8s-pv-onefs", "k8s-pv-gpfs", "cephfs-dms"
GPFS_A = "pvc-52c6132d-bce6-4cbe-842a-dbe35245ab2c/pvc-52c6132d-bce6-4cbe-842a-dbe35245ab2c-data"
GPFS_B = "pvc-b0b6e65b-f74a-4998-8f84-e1ae6047deb8/pvc-b0b6e65b-f74a-4998-8f84-e1ae6047deb8-data"
ONEFS_ROOT = "data/csi/k8s-3fa9c2d71e"
NFS_DATASETS_VOL = "pvc-814fe02b-ffe7-4a17-8430-b8de9a6c0b91"
TARGETS = "/api/admin/usage/targets"
FACETS = "/api/admin/usage/facets"


def _pvb(cluster, ns, pvc, volume, sc, root):
    return {"cluster": cluster, "namespace": ns, "pvc": pvc, "volume": volume, "storage_class": sc, "root": root,
            "path": ""}


_DATASETS = _pvb("dmsk8s", "team-a", "datasets", NFS_DATASETS_VOL, "tb-nfs", "team-a/datasets")
_WEIGHTS = _pvb("dmsk8s2", "lab-y", "weights", "pvc-c2-weights", "c2-nfs", "c2/lab-y/weights")
_GONE = _pvb("dmsk8s", "team-d", "old", "pvc-gone", "tb-nfs", "team-d/old")
# (storage, target, requester, 완료 시각, pv 블록 | None = 직접 경로, 실 사용량)
_SEED = [
    (NFS, "team-d/old/run", "alice", "2026-10-24T00:00:00Z", _GONE, 50),
    (NFS, "team-a/datasets/dms-proof", "alice", "2026-10-25T01:00:00Z", _DATASETS, 500),
    (NFS, "team-a/datasets/proof-scan", "mason", "2026-10-25T02:00:00Z", _DATASETS, 100),
    (NFS, "c2/lab-y/weights", "mason", "2026-10-25T03:00:00Z", _WEIGHTS, 900),
    (NFS, "proof-d9/alice/latest", "mason", "2026-10-26T01:00:00Z", None, 300),
    (NFS, "proof-nl/a/selfnl", "mason", "2026-10-26T02:00:00Z", None, 200),
    (NFS, "team-a/datasets/proof-ov", "mason", "2026-10-26T03:00:00Z", None, 700),      # PV 루트 아래 직접 경로
    (NFS, "c2/lab-y", "mason", "2026-10-26T04:00:00Z", None, 1000),                     # PV 루트의 상위 = PV 밖
    (ONEFS, ONEFS_ROOT + "/dms-proof", "alice", "2026-10-25T05:00:00Z",
     _pvb("dmsk8s", "team-a", "kraken-kv-cache", "k8s-3fa9c2d71e", "tb-onefs", ONEFS_ROOT), 10),
    (ONEFS, ONEFS_ROOT + "/dms-proof", "junwoo.song", "2026-10-26T05:00:00Z",
     _pvb("dmsk8s", "team-a", "kraken-kv-cache", "k8s-3fa9c2d71e", "tb-onefs", ONEFS_ROOT), 20),
    (GPFS, GPFS_A + "/dms-proof", "mason", "2026-10-25T06:00:00Z",
     _pvb("dmsk8s", "team-a", "models", GPFS_A.split("/")[0], "tb-gpfs", GPFS_A), 1),
    (GPFS, GPFS_B + "/run", "alice", "2026-10-25T07:00:00Z",
     _pvb("dmsk8s", "team-b", "models", GPFS_B.split("/")[0], "tb-gpfs", GPFS_B), 2),
    (GPFS, "plain/dir", "mason", "2026-10-26T06:00:00Z", None, 3),
    (CEPH, "proj/x", "mason", "2026-10-26T07:00:00Z", None, 4),
    (CEPH, "proj/y", "alice", "2026-10-26T08:00:00Z", None, 5),
]
# k8s-pv-nfs 의 최근 스캔 내림차순
NFS_DESC = ["c2/lab-y", "team-a/datasets/proof-ov", "proof-nl/a/selfnl", "proof-d9/alice/latest", "c2/lab-y/weights",
            "team-a/datasets/proof-scan", "team-a/datasets/dms-proof", "team-d/old/run"]


def _c2_snapshot():
    """두 번째 클러스터 dmsk8s2 -- 테스트베드 실데이터 항목을 고쳐 만든다(SC c2-nfs, PVC lab-y/weights, cocoa.song 의 lab-y 바인딩)."""
    raw = {key: json.loads((FIXTURES / name).read_text())["items"] for key, (name, _) in _FILES.items()}

    def pick(key, ns, name):
        return copy.deepcopy(next(i for i in raw[key]
                                  if i["metadata"].get("namespace") == ns and i["metadata"]["name"] == name))
    sc = pick("storage_classes", None, "tb-nfs")
    sc["metadata"]["name"] = "c2-nfs"
    pvc = pick("pvcs", "team-b", "scratch")
    pvc["metadata"].update(namespace="lab-y", name="weights", uid="c2-weights-uid")
    pvc["spec"].update(storageClassName="c2-nfs", volumeName="pvc-c2-weights")
    pv = pick("pvs", None, "pvc-aa3d0bd2-0219-4ac1-aa57-2cf64e16770c")
    pv["metadata"]["name"] = "pvc-c2-weights"
    pv["spec"]["storageClassName"] = "c2-nfs"
    pv["spec"]["claimRef"].update(namespace="lab-y", name="weights")
    rb = pick("role_bindings", "team-b", "cocoa-song-clusterrole-edit")
    rb["metadata"]["namespace"] = "lab-y"
    return normalize_snapshot({"storage_classes": [project_storage_class(sc)], "pvcs": [project_pvc(pvc)],
                               "pvs": [project_pv(pv)], "role_bindings": [project_role_binding(rb)]})


def _seed_scan(repos, db, art, *, storage, target, requester, at, pv=None, total=None):
    payload = {"storage": storage, "target": target, **({"pv": pv} if pv is not None else {})}
    rid = repos.requests.create(operation="scan", requester_id=requester, actor=requester,
                                resource_key=f"k:{uuid.uuid4().hex}", payload=payload, priority="mid",
                                auth_method="session",
                                target_label=f"{pv['pvc']} — {pv['namespace']} ({pv['storage_class']})" if pv else None)
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(
        rid, plan_id, operation="scan", priority="mid", storage_name=storage, target=target, options={}, tool="dscan",
        worker_pool={"identity": {"username": requester, "uid": os.getuid(), "gid": os.getgid()}}, precondition={},
        actor="planner")
    repos.data_jobs.set_job_state(jid, DataJobState.SUCCEEDED, actor="stepper")
    db.execute("UPDATE data_jobs SET updated_at = :t WHERE job_id = :j", {"t": at, "j": jid})
    if total is not None:
        d = art / jid / "execution"
        d.mkdir(parents=True)
        (d / "dscan-report.json").write_text(json.dumps(_report(mtime_bytes=(total,))))
    return jid


@pytest.fixture
def uw(world, db, tmp_path):
    """테스트베드 모양 사용량 세계(위 절 머리말). 관리자 세션(ops -- 특권 목록)으로 로그인한 client."""
    from dms.api.app import create_app
    repos = world
    _sync_cluster(repos, db, name="dmsk8s2", data=_c2_snapshot())
    repos.k8s.set_sc_map("c2-nfs", storage_name=NFS, path_template="c2/{namespace}/{pvc}", actor="ops")
    assert repos.k8s.pv_roots(NFS) == ("c2/lab-y/weights", "team-a/datasets", "team-b/scratch", "team-c/archive")
    art = tmp_path / "artifacts"
    jobs = {}
    for storage, target, requester, at, pv, total in _SEED:
        jobs[(storage, target, requester)] = _seed_scan(repos, db, art, storage=storage, target=target,
                                                        requester=requester, at=at, pv=pv, total=total)
    client = TestClient(create_app(_k8s_settings(artifact_base_uri=f"file://{art}"), db))
    assert client.post("/api/auth/login", json={"username": "ops", "password": "pw"}).status_code == 200
    return SimpleNamespace(client=client, repos=repos, db=db, art=art, jobs=jobs)


def _page(uw, storage=NFS, **params):
    r = uw.client.get(TARGETS, params={"storage": storage, **params})
    assert r.status_code == 200, r.text
    return r.json()


def _names(uw, storage=NFS, **params):
    body = _page(uw, storage, **params)
    names = [row["target"] for row in body["rows"]]
    if not body["window_truncated"]:
        assert body["matched_count"] == len(names), (params, body["matched_count"])   # 상한(100) 안 -- 다 보인다
    return names


_NO_FILTERS = dict.fromkeys(("cluster", "namespace", "pvc", "storage_class", "user", "scope", "requester"))


def test_targets_envelope_echoes_filters_and_carries_pv_on_every_row(uw):
    body = _page(uw)
    assert list(body) == ["rows", "matched_count", "window_limit", "window_truncated", "filters"]
    assert (body["matched_count"], body["window_limit"], body["window_truncated"]) == (8, 10000, False)
    assert body["filters"] == {"storage": NFS, "path": None, "q": None, **_NO_FILTERS}
    rows = {r["target"]: r for r in body["rows"]}
    assert [r["target"] for r in body["rows"]] == NFS_DESC
    assert list(body["rows"][0]) == ["storage_name", "target", "scan_count", "first_scan_at", "last_scan_at", "latest",
                                     "pv"]
    datasets = [{"cluster": "dmsk8s", "namespace": "team-a", "pvc": "datasets", "volume": NFS_DATASETS_VOL,
                 "storage_class": "tb-nfs"}]
    # PV 루트 아래 직접 경로 scan 도 그 PV 다(경로로 판정 -- 블록이 없어도)
    assert rows["team-a/datasets/proof-ov"]["pv"] == {"source": "inventory", "root": "team-a/datasets",
                                                       "volumes": datasets, "users": ["alice", "junwoo.song"]}
    assert rows["c2/lab-y/weights"]["pv"] == {
        "source": "inventory", "root": "c2/lab-y/weights", "users": ["cocoa.song"],
        "volumes": [{"cluster": "dmsk8s2", "namespace": "lab-y", "pvc": "weights", "volume": "pvc-c2-weights",
                     "storage_class": "c2-nfs"}]}
    assert rows["team-d/old/run"]["pv"] == {           # 지운 PVC -- PV 주소 scan 기록, 지금 바인딩 없음
        "source": "recorded", "root": "team-d/old", "users": [],
        "volumes": [{"cluster": "dmsk8s", "namespace": "team-d", "pvc": "old", "volume": "pvc-gone",
                     "storage_class": "tb-nfs"}]}
    for outside in ("c2/lab-y", "proof-d9/alice/latest", "proof-nl/a/selfnl"):
        assert rows[outside]["pv"] == {"source": "outside", "root": None, "volumes": [], "users": []}
    # 옛 scan-targets 의 행과 공개 열·latest 가 같다(새 봉투는 pv 를 더했을 뿐)
    old = uw.client.get("/api/admin/usage/scan-targets", params={"storage": NFS}).json()
    assert [{k: v for k, v in r.items() if k != "pv"} for r in body["rows"]] == old


def test_each_filter_selects_whole_targets(uw):
    get = lambda storage=NFS, **p: _names(uw, storage, **p)   # noqa: E731
    team_a = ["team-a/datasets/proof-ov", "team-a/datasets/proof-scan", "team-a/datasets/dms-proof"]
    assert get(namespace="team-a") == team_a
    assert get(cluster="dmsk8s", namespace="team-a", pvc="datasets") == team_a
    assert get(cluster="dmsk8s2") == ["c2/lab-y/weights"]
    assert get(storage_class="c2-nfs") == ["c2/lab-y/weights"]
    assert get(namespace="team-d") == ["team-d/old/run"]                      # 기록 대체
    assert get(scope="outside") == ["c2/lab-y", "proof-nl/a/selfnl", "proof-d9/alice/latest"]
    assert get(scope="pv") == ["team-a/datasets/proof-ov", "c2/lab-y/weights", "team-a/datasets/proof-scan",
                               "team-a/datasets/dms-proof", "team-d/old/run"]
    assert get(scope="unknown") == []
    assert get(user="cocoa.song") == ["c2/lab-y/weights"]                     # 권한 사용자(지금 RoleBinding)
    assert get(user="alice") == team_a
    assert get(requester="alice") == ["team-a/datasets/dms-proof", "team-d/old/run"]
    assert get(requester="mason", namespace="team-a") == ["team-a/datasets/proof-ov", "team-a/datasets/proof-scan"]
    assert get(path="proof", scope="outside") == ["proof-nl/a/selfnl", "proof-d9/alice/latest"]
    assert get(scope="outside", cluster="dmsk8s") == []                       # 밖 + PV 축 = 0건(정상값)
    assert get(cluster="dmsk8s", namespace="lab-y") == []                     # 축은 같은 정체 하나가 만족해야 한다
    assert get(user="cocoa.song", cluster="dmsk8s") == []
    # PVC 이름(화면 「PV 이름」)은 namespace 마다만 유일 -- models 가 team-a·team-b 둘 다
    assert get(GPFS, pvc="models") == [GPFS_B + "/run", GPFS_A + "/dms-proof"]
    assert get(GPFS, pvc="models", namespace="team-b") == [GPFS_B + "/run"]
    assert get(GPFS, scope="outside") == ["plain/dir"]                        # both 스토리지의 PV 밖
    assert get(CEPH, scope="outside") == ["proj/y", "proj/x"] and get(CEPH, scope="pv") == []
    body = _page(uw, namespace="team-a", requester="mason", path="proof")
    assert body["filters"] == {"storage": NFS, "path": "proof", "q": None, **_NO_FILTERS, "namespace": "team-a",
                               "requester": "mason"}


def test_requester_filter_is_per_target_and_rows_keep_their_latest(uw):
    # onefs 의 한 대상을 alice(옛)·junwoo.song(최신)이 PV 주소로 scan -- alice 로 걸러도 행은 그 대상의 전체 기록이다.
    [row] = _page(uw, ONEFS, requester="alice")["rows"]
    assert row["target"] == ONEFS_ROOT + "/dms-proof" and row["scan_count"] == 2
    assert row["latest"]["requester"] == "junwoo.song" and row["latest"]["total_bytes"] == 20
    assert row["pv"]["volumes"][0]["pvc"] == "kraken-kv-cache"
    assert _page(uw, ONEFS, requester="junwoo.song")["rows"] == [row]
    assert _page(uw, ONEFS, requester="nobody") == {
        "rows": [], "matched_count": 0, "window_limit": 10000, "window_truncated": False,
        "filters": {"storage": ONEFS, "path": None, "q": None, **_NO_FILTERS, "requester": "nobody"}}


def test_unsynced_cluster_makes_the_rest_unknown_and_deleted_clusters_fall_back_to_records(uw):
    repos = uw.repos
    repos.k8s.create_cluster(name="dmsk8s3", api_server="https://10.10.10.30:6443", tls_server_name=None, ca_pem=None,
                             insecure_skip_tls_verify=False, token_enc="v1:x", enabled=False, actor="ops")
    # 한 번도 적용되지 않은 클러스터(비활성이어도)의 PV 는 모른다 -- 「PV 밖」이 아니라 「모름」(null ≠ 0)
    assert _names(uw, scope="unknown") == ["c2/lab-y", "proof-nl/a/selfnl", "proof-d9/alice/latest"]
    assert _names(uw, scope="outside") == []
    assert _names(uw, CEPH, scope="outside") == ["proj/y", "proj/x"]            # filesystem 은 그대로 밖
    repos.k8s.delete_cluster("dmsk8s3", actor="ops")
    # 클러스터를 지우면 그 PV 는 인벤토리에서 바로 사라진다 -- PV 주소 scan 기록으로 산다(지금 바인딩은 없다)
    repos.k8s.delete_cluster("dmsk8s2", actor="ops")
    [row] = _page(uw, cluster="dmsk8s2")["rows"]
    assert row["target"] == "c2/lab-y/weights"
    assert (row["pv"]["source"], row["pv"]["root"], row["pv"]["users"]) == ("recorded", "c2/lab-y/weights", [])
    assert _names(uw, user="cocoa.song") == []


def test_duplicate_bindings_count_a_user_once(uw):
    # 같은 (cluster, namespace, user)가 이름만 다른 바인딩으로 둘(테스트베드 실모양 -- clusterrole + namespaceAdmin)
    uw.db.execute("""INSERT INTO k8s_role_bindings (cluster_name, namespace, name, role_kind, role_name, subjects, dms_user,
                                                    access, subject_mismatch)
                     VALUES ('dmsk8s', 'team-a', 'alice-dup', 'ClusterRole', 'kubeflow-admin', '[]', 'alice', 'admin', 0)""")
    rows = {r["target"]: r for r in _page(uw)["rows"]}
    assert rows["team-a/datasets/dms-proof"]["pv"]["users"] == ["alice", "junwoo.song"]
    users = {u["dms_user"]: u["target_count"] for u in uw.client.get(FACETS, params={"storage": NFS}).json()["users"]}
    assert users == {"alice": 3, "junwoo.song": 3, "cocoa.song": 1}


def test_pv_window_is_the_recent_window_whatever_the_sort(uw, monkeypatch):
    # 2026-10-29 리뷰: PV 판정 창은 정렬과 무관하게 최근 스캔 순(창 W + 1) -- 정렬은 순서만 바꾸고 걸리는 **집합**은 그대로라
    # facets 의 옵션 수(같은 창)·필터 합계·포탈의 「최근 스캔된 W개 안에서」와 한 규칙이다. 예전엔 요청한 정렬로 창을 잘라
    # 「오래된 순」이면 가장 오래된 W개를 판정했다(같은 필터가 정렬만 바꿔도 다른 대상을 냈다).
    from dms.api import routes_usage
    monkeypatch.setattr(routes_usage, "_WINDOW_LIMIT", 5)
    every_asc = _names(uw, order="asc")                         # SQL 경로(필터 없음) -- 창과 무관하게 정확
    assert every_asc == NFS_DESC[::-1]
    recent = NFS_DESC[:5]                                       # 최근 스캔된 5개 = 판정 창
    facets = uw.client.get(FACETS, params={"storage": NFS}).json()
    assert facets["window_truncated"] is True
    assert (facets["scope"]["inventory"], facets["scope"]["outside"]) == (2, 3)
    for scope, want in (("pv", ["team-a/datasets/proof-ov", "c2/lab-y/weights"]),
                        ("outside", ["c2/lab-y", "proof-nl/a/selfnl", "proof-d9/alice/latest"])):
        for sort, order in (("last_scan", "desc"), ("last_scan", "asc"), ("scans", "desc"), ("scans", "asc")):
            body = _page(uw, scope=scope, sort=sort, order=order)
            # 창이 넘쳤으니 맞는 수는 모름(0 이 아니다)
            assert (body["window_truncated"], body["matched_count"], body["window_limit"]) == (True, None, 5)
            got = [r["target"] for r in body["rows"]]
            assert set(got) == set(want) and set(got) <= set(recent), (scope, sort, order)
            # 창 안의 순서 = 그 정렬의 SQL 순서(필터 없는 SQL 경로에서 같은 대상만 남긴 것)
            assert got == [t for t in _names(uw, sort=sort, order=order) if t in set(got)], (scope, sort, order)
    # 내보내기도 같은 창이다(오래된 순 내보내기도 최근 창의 대상 -- 포탈 CSV 문구 「최근 스캔된 W개 안에서만 걸렀습니다」)
    parts, err = _drain_targets_export(uw.client, order="asc", flt=_flt(scope="outside"))
    body = json.loads(b"".join(parts))
    assert err is None and (body["truncated"], body["matched_count"]) == (True, None)
    assert [r["target"] for r in body["rows"]] == ["proof-d9/alice/latest", "proof-nl/a/selfnl", "c2/lab-y"]
    # 요청자·경로는 SQL 이라 창과 무관하게 정확하다
    body = _page(uw, requester="mason")
    assert (body["window_truncated"], body["matched_count"], len(body["rows"])) == (False, 6, 6)


def test_window_order_matches_the_sql_order_with_ties(db):
    # _window_order 는 문자열을 비교하지 않고 최근 스캔 순 창을 요청한 SQL 정렬로 놓는다 -- 같은 시각·같은 스캔 횟수 동률이 이름
    # 순(방언 콜레이션)으로 갈리는 자리까지 SQL 결과와 같아야 한다(DMS_TEST_DB=postgresql 이면 PG 콜레이션으로도 돈다).
    from dms.api.routes_usage import _window_order
    from dms.repositories import Repositories
    repos = Repositories(db)
    # (target, 스캔 시각들) -- 대문자·밑줄·숫자가 섞인 이름(콜레이션마다 순서가 갈리는 글자), 같은 최신 시각 묶음 둘
    seed = [("b", ["01", "05"]), ("A", ["05"]), ("a_1", ["02", "03", "05"]), ("a-1", ["05"]), ("Z9", ["04"]),
            ("c", ["04", "04"]), ("10", ["02"]), ("9", ["02", "03"])]
    for target, ats in seed:
        for at in ats:
            jid = uuid.uuid4().hex
            db.execute("""INSERT INTO data_jobs (job_id, request_id, operation, tool, storage_name, target, options,
                                                 priority, state, worker_pool, created_at, updated_at)
                          VALUES (:j, :r, 'scan', 'dscan', 's1', :t, '{}', 'mid', 'Succeeded', '{}', :at, :at)""",
                       {"j": jid, "r": uuid.uuid4().hex, "t": target, "at": f"2026-09-01T00:00:{at}Z"})
    window = repos.data_jobs.scan_target_heads(storage="s1", order="desc", limit=None)
    for sort in ("last_scan", "scans"):
        for order in ("asc", "desc"):
            want = [h["target"] for h in repos.data_jobs.scan_target_heads(storage="s1", order=order, sort=sort,
                                                                           limit=None)]
            assert [h["target"] for h in _window_order(window, sort, order)] == want, (sort, order)
    assert [h["target"] for h in window] != [h["target"] for h in _window_order(window, "last_scan", "asc")][::-1]


def test_metric_sort_with_a_pv_filter_ranks_by_the_shown_value(uw, monkeypatch):
    rows = _page(uw, scope="pv", sort="bytes", order="desc")["rows"]
    assert [r["latest"]["total_bytes"] for r in rows] == [900, 700, 500, 100, 50]     # 정렬 값 = 보이는 값
    assert [r["target"] for r in rows] == ["c2/lab-y/weights", "team-a/datasets/proof-ov",
                                           "team-a/datasets/dms-proof", "team-a/datasets/proof-scan", "team-d/old/run"]
    rows = _page(uw, scope="pv", sort="bytes", order="asc", limit=2)["rows"]
    assert [r["latest"]["total_bytes"] for r in rows] == [50, 100]
    from dms.api import routes_usage
    monkeypatch.setattr(routes_usage, "_WINDOW_LIMIT", 3)
    body = _page(uw, scope="pv", sort="bytes")          # 지표 정렬의 판정 창은 최근 스캔 순 -- 그 안의 PV 대상만
    assert [r["target"] for r in body["rows"]] == ["team-a/datasets/proof-ov"] and body["matched_count"] is None


@pytest.mark.parametrize("bad", [
    {"cluster": ""}, {"namespace": ""}, {"pvc": ""}, {"storage_class": ""}, {"user": ""}, {"requester": ""},
    {"scope": ""}, {"scope": "inside"}, {"cluster": "a\x00b"}, {"user": "al\nice"}, {"requester": "x\x7f"},
    {"namespace": "n" * 254}, {"user": "u" * 254}, {"requester": "r" * 513},
])
def test_bad_filter_values_are_422_not_ignored(uw, bad):
    for url in (TARGETS, TARGETS + "/export", TOTALS):
        assert uw.client.get(url, params={"storage": NFS, **bad}).status_code == 422, (url, bad)


@pytest.mark.parametrize("url,params", [
    (TARGETS, {"storage": "a\x00b"}), (TARGETS, {"storage": NFS, "path": "a\x00b"}),
    (TARGETS + "/export", {"storage": "a\x00b"}), (TARGETS + "/export", {"storage": NFS, "path": "a\x00b"}),
    (FACETS, {"storage": "a\x00b"}),
    (TOTALS, {"storage": "a\x00b"}), (TOTALS, {"storage": "a\x00b", "requester": "alice"}),
])
def test_new_endpoints_reject_nul_in_storage_and_path(uw, url, params):
    # 2026-10-29 2차 리뷰: 새 엔드포인트의 storage·path 에 NUL 이 실리면 PG 는 psycopg DataError → 500 이었다(sqlite 는 조용히 빈
    # 결과 -- 방언마다 달랐다). 이제 둘 다 422. 빈 문자열·다른 제어문자는 이름이다(구형·변조 행의 이름 -- 그 탭을 지울 수 있어야 한다).
    r = uw.client.get(url, params=params)
    assert r.status_code == 422, (url, params, r.status_code, r.text[:200])
    for ok in ({"storage": ""}, {"storage": "a\x01b"}, {"storage": NFS, "path": ""}, {"storage": NFS, "path": "a\tb"}):
        assert uw.client.get(url, params={**ok, **{k: v for k, v in params.items() if k not in ("storage", "path")}}
                             ).status_code == 200, (url, ok)


def _resync_with_binding(uw, name, *, namespace="team-a"):
    """dmsk8s 를 테스트베드 실데이터 + RoleBinding 하나(alice 의 team-a 바인딩을 복제해 이름만 바꾼 것)로 다시 동기화한다."""
    out = {}
    for key, (fname, project) in _FILES.items():
        items = json.loads((FIXTURES / fname).read_text())["items"]
        if key == "role_bindings":
            rb = copy.deepcopy(next(i for i in items if i["metadata"]["name"] == "alice-clusterrole-edit"))
            rb["metadata"].update(name=name, namespace=namespace, uid=f"uid-{uuid.uuid4().hex}")
            items = items + [rb]
        out[key] = [project(i) for i in items]
    got = uw.repos.k8s.claim(node_name="k8s-w1", role="primary", now="2026-10-25T14:30:00Z", interval=300,
                             primary_configured=True, token_ok=lambda e: True, deadline_seconds=120)
    assert got["run"]["cluster_name"] == "dmsk8s"
    run_id = got["run"]["run_id"]
    uw.repos.k8s.receive(run_id, node_name="k8s-w1", now="2026-10-25T14:30:05Z", ok=True,
                         data=normalize_snapshot(out), fingerprint="fp-long")
    assert uw.repos.k8s.apply_run(run_id)["outcome"] == "applied"


def test_every_facet_value_passes_its_own_filter(uw):
    """/facets 가 내는 선택지는 같은 API 의 필터가 받고, 그 옵션의 대상 수 = 그 옵션을 고른 목록의 matched_count 다(창이 넘치지 않는
    세계). 2026-10-29 2차 리뷰: 권한 사용자는 바인딩 이름(≤253, k8s_sync._MAX_NAME)에서 파생해 최대 236자인데 user 필터 상한이 128 이라
    facets 가 낸 값을 422 로 거절했다(포탈에서 고르는 순간 오류). 요청자도 같은 성질 -- 옛 공유 토큰 제출의 `node:<노드 이름 ≤254>`
    (259자, api.auth). 둘 다 생산자 상한의 값으로 고정한다. (제어문자가 든 RoleBinding 이름 -- k8s 는 RBAC 이름에 '/'·'%' 만 막는다 --
    에서 파생한 dms_user 는 필터 패턴이 받지 않는다: 계정 아이디 규칙 밖이라 실 사용자가 아니다.)"""
    long_id = "long-" + "x" * 231                                   # 바인딩 이름 = 236 + 17 = 253자(k8s 이름 상한 그대로)
    long_user = long_id.replace("-", ".")
    _resync_with_binding(uw, long_id + "-clusterrole-edit")
    long_requester = "node:" + "n" * 254
    rid = uw.db.query_one("SELECT request_id FROM data_jobs WHERE job_id = :j",
                          {"j": uw.jobs[(NFS, "proof-nl/a/selfnl", "mason")]})["request_id"]
    uw.db.execute("UPDATE requests SET requester_id = :q WHERE request_id = :r", {"q": long_requester, "r": rid})

    seen_long = set()
    for storage in (NFS, ONEFS, GPFS, CEPH):
        f = uw.client.get(FACETS, params={"storage": storage}).json()
        assert f["window_truncated"] is False
        options = ([({"cluster": c["cluster"]}, c["target_count"]) for c in f["clusters"]]
                   + [({"cluster": x["cluster"], "namespace": x["namespace"]}, x["target_count"]) for x in f["namespaces"]]
                   + [({"cluster": x["cluster"], "namespace": x["namespace"], "pvc": x["pvc"]}, x["target_count"])
                      for x in f["pvcs"]]
                   + [({"storage_class": x["storage_class"]}, x["target_count"]) for x in f["storage_classes"]]
                   + [({"user": x["dms_user"]}, x["target_count"]) for x in f["users"]]
                   + [({"requester": x["requester_id"]}, x["target_count"]) for x in f["requesters"]]
                   + [({"scope": s}, n) for s, n in (("pv", f["scope"]["inventory"] + f["scope"]["recorded"]),
                                                      ("outside", f["scope"]["outside"]),
                                                      ("unknown", f["scope"]["unknown"])) if n > 0])
        assert options
        for params, count in options:
            r = uw.client.get(TARGETS, params={"storage": storage, "limit": 200, **params})
            assert r.status_code == 200, (storage, params, r.text[:200])
            assert r.json()["matched_count"] == count, (storage, params)
            if long_user in params.values() or long_requester in params.values():
                seen_long.update(params.values())
                for url in (TARGETS + "/export", TOTALS):
                    assert uw.client.get(url, params={"storage": storage, **params}).status_code == 200, (url, params)
    assert {long_user, long_requester} <= seen_long                 # 긴 값이 실제로 선택지에 나왔다


def test_new_endpoints_need_a_storage_and_an_admin(uw):
    for url in (TARGETS, TARGETS + "/export", FACETS):
        assert uw.client.get(url).status_code == 422
    assert uw.client.get(TARGETS, params={"storage": ""}).json()["rows"] == []     # 빈 이름도 이름이다
    uw.client.cookies.clear()
    assert uw.client.post("/api/auth/login", json={"username": "alice", "password": "pw"}).status_code == 200
    for url in (TARGETS, TARGETS + "/export", FACETS):
        assert uw.client.get(url, params={"storage": NFS}).status_code == 403


def _flt(**kw):
    from dms.api.routes_usage import AttrFilter
    from dms.usage_attribution import PvFilter
    requester = kw.pop("requester", None)
    return AttrFilter(pv=PvFilter(**kw), requester=requester)


def _drain_targets_export(client, **params):
    """새 /targets/export 를 직접 부른다(_drain_export 와 같은 길) -- 인자는 전부 명시한다(Query 기본값 객체가 새지 않게)."""
    import asyncio
    from dms.api import routes_usage
    args = {"storage": NFS, "path": None, "q": None, "order": "desc", "sort": "last_scan", "flt": _flt(), **params}
    resp = routes_usage.export_targets(SimpleNamespace(app=client.app), **args)
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


@pytest.mark.parametrize("sort", ["last_scan", "bytes"])
def test_targets_export_streams_with_filters_echo_and_pv_last(uw, monkeypatch, sort):
    from dms.api import routes_usage
    monkeypatch.setattr(routes_usage, "_CHUNK", 2)
    parts, err = _drain_targets_export(uw.client, sort=sort)
    assert err is None and len(parts) == 1 + 4 + 1                     # 머리 + 묶음 4 + 꼬리(count)
    assert parts[0].startswith(b'{"generated_at":') and parts[0].endswith(b'"rows":[')
    assert b'"truncated":false,"capped":false,"matched_count":8,"filters":{"storage":"k8s-pv-nfs","path":null,' in parts[0]
    assert parts[-1] == b'],"count":8}'
    body = json.loads(b"".join(parts))
    assert list(body) == ["generated_at", "truncated", "capped", "matched_count", "filters", "rows", "count"]
    assert all(list(r)[-3:] == ["latest", "previous", "pv"] for r in body["rows"])
    by = {r["target"]: r for r in body["rows"]}
    assert by["team-d/old/run"]["pv"]["source"] == "recorded" and by["c2/lab-y"]["pv"]["source"] == "outside"
    assert by["team-a/datasets/dms-proof"]["storage"]["backend_type"] == "nfs"
    # 필터가 걸리면 같은 판정으로 거른 행만
    parts, err = _drain_targets_export(uw.client, sort=sort, flt=_flt(user="alice"), path="proof")
    body = json.loads(b"".join(parts))
    assert sorted(r["target"] for r in body["rows"]) == ["team-a/datasets/dms-proof", "team-a/datasets/proof-ov",
                                                         "team-a/datasets/proof-scan"]
    assert body["matched_count"] == body["count"] == 3 and body["filters"]["user"] == "alice"
    # HTTP 로도 같은 모양
    r = uw.client.get(TARGETS + "/export", params={"storage": NFS, "sort": sort, "scope": "outside"})
    assert r.headers["content-type"] == "application/json"
    assert [x["target"] for x in r.json()["rows"]] == (
        ["c2/lab-y", "proof-nl/a/selfnl", "proof-d9/alice/latest"] if sort == "last_scan"
        else ["c2/lab-y", "proof-d9/alice/latest", "proof-nl/a/selfnl"])


def test_targets_export_truncation_is_decided_before_the_first_byte(uw, monkeypatch):
    from dms.api import routes_usage
    monkeypatch.setattr(routes_usage, "_WINDOW_LIMIT", 3)
    # PV 경로: 판정 창이 넘쳤다 -- 맞는 수는 모름
    parts, err = _drain_targets_export(uw.client, flt=_flt(scope="pv"))
    assert err is None and b'"truncated":true,"capped":false,"matched_count":null,' in parts[0]
    assert json.loads(b"".join(parts))["count"] == 1                    # 창(최근 3) 안의 PV 대상은 proof-ov 하나
    # SQL 경로(요청자): 맞는 대상이 상한을 넘었다 -- 맞는 수는 정확하다
    parts, err = _drain_targets_export(uw.client, flt=_flt(requester="mason"))
    assert b'"truncated":true,"capped":true,"matched_count":6,' in parts[0] and parts[-1] == b'],"count":3}'
    monkeypatch.setattr(routes_usage, "_WINDOW_LIMIT", 6)
    parts, err = _drain_targets_export(uw.client, flt=_flt(requester="mason"))
    assert b'"truncated":false,"capped":false,"matched_count":6,' in parts[0]


def test_targets_export_failure_mid_stream_is_broken_json(uw, monkeypatch):
    from dms.api import routes_usage
    monkeypatch.setattr(routes_usage, "_CHUNK", 2)
    real = uw.repos.data_jobs.recent_scans_for_targets
    app_repos = uw.client.app.state.repos
    calls = []

    def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("db gone")
        return real(*args, **kwargs)
    monkeypatch.setattr(app_repos.data_jobs, "recent_scans_for_targets", flaky)
    parts, err = _drain_targets_export(uw.client)
    assert isinstance(err, RuntimeError) and len(parts) == 2
    with pytest.raises(ValueError):
        json.loads(b"".join(parts))


def test_facets_count_targets_once_per_option(uw, monkeypatch):
    r = uw.client.get(FACETS, params={"storage": NFS})
    assert r.status_code == 200, r.text
    f = r.json()
    assert list(f) == ["storage_name", "computed_at", "window_limit", "window_truncated", "target_count", "scope",
                       "clusters", "namespaces", "pvcs", "storage_classes", "users", "requesters", "lists_truncated"]
    assert (f["storage_name"], f["window_limit"], f["window_truncated"], f["target_count"]) == (NFS, 10000, False, 8)
    assert f["scope"] == {"inventory": 4, "recorded": 1, "outside": 3, "unknown": 0}
    assert f["clusters"] == [{"cluster": "dmsk8s", "target_count": 4}, {"cluster": "dmsk8s2", "target_count": 1}]
    assert f["namespaces"] == [{"cluster": "dmsk8s", "namespace": "team-a", "target_count": 3},
                               {"cluster": "dmsk8s", "namespace": "team-d", "target_count": 1},
                               {"cluster": "dmsk8s2", "namespace": "lab-y", "target_count": 1}]
    assert f["pvcs"] == [
        {"cluster": "dmsk8s", "namespace": "team-a", "pvc": "datasets", "storage_class": "tb-nfs", "target_count": 3},
        {"cluster": "dmsk8s", "namespace": "team-d", "pvc": "old", "storage_class": "tb-nfs", "target_count": 1},
        {"cluster": "dmsk8s2", "namespace": "lab-y", "pvc": "weights", "storage_class": "c2-nfs", "target_count": 1}]
    assert f["storage_classes"] == [{"storage_class": "tb-nfs", "target_count": 4},
                                    {"storage_class": "c2-nfs", "target_count": 1}]
    assert f["users"] == [{"dms_user": "alice", "target_count": 3}, {"dms_user": "junwoo.song", "target_count": 3},
                          {"dms_user": "cocoa.song", "target_count": 1}]
    assert f["requesters"] == [{"requester_id": "mason", "target_count": 6}, {"requester_id": "alice", "target_count": 2}]
    assert f["lists_truncated"] == dict.fromkeys(
        ("clusters", "namespaces", "pvcs", "storage_classes", "users", "requesters"), False)
    # 같은 PVC 이름(models)이 두 namespace -- 선택지는 (cluster, namespace, pvc) 마다
    g = uw.client.get(FACETS, params={"storage": GPFS}).json()
    assert [(p["namespace"], p["pvc"]) for p in g["pvcs"]] == [("team-a", "models"), ("team-b", "models")]
    assert g["scope"] == {"inventory": 2, "recorded": 0, "outside": 1, "unknown": 0}
    # filesystem 탭: PV 선택지가 없다(포탈은 요청자만 남긴다)
    c = uw.client.get(FACETS, params={"storage": CEPH}).json()
    assert c["scope"] == {"inventory": 0, "recorded": 0, "outside": 2, "unknown": 0}
    assert c["clusters"] == c["namespaces"] == c["pvcs"] == c["storage_classes"] == c["users"] == []
    assert c["requesters"] == [{"requester_id": "alice", "target_count": 1}, {"requester_id": "mason", "target_count": 1}]
    # 목록 상한 -- 대상 수 내림차순으로 자르고 잘렸다고 말한다
    from dms.api import routes_usage
    monkeypatch.setattr(routes_usage, "FACET_LIST_CAP", 1)
    f = uw.client.get(FACETS, params={"storage": NFS}).json()
    assert f["clusters"] == [{"cluster": "dmsk8s", "target_count": 4}]
    assert f["requesters"] == [{"requester_id": "mason", "target_count": 6}]
    assert f["lists_truncated"] == {"clusters": True, "namespaces": True, "pvcs": True, "storage_classes": True,
                                    "users": True, "requesters": True}


def test_facets_requesters_are_storage_wide_while_pv_counts_follow_the_window(uw, monkeypatch):
    from dms.api import routes_usage
    monkeypatch.setattr(routes_usage, "_WINDOW_LIMIT", 3)
    f = uw.client.get(FACETS, params={"storage": NFS}).json()
    assert (f["window_truncated"], f["target_count"]) == (True, 3)
    assert f["scope"] == {"inventory": 1, "recorded": 0, "outside": 2, "unknown": 0}   # 최근 3개(c2/lab-y·proof-ov·selfnl)
    assert f["requesters"] == [{"requester_id": "mason", "target_count": 6}, {"requester_id": "alice", "target_count": 2}]


def test_filtered_storage_totals_sum_the_top_of_the_filtered_set(uw):
    whole = _totals(uw.client, NFS)                                  # 필터 없음 -- 모양·요청이 예전 그대로
    assert "filters" not in whole and whole["target_count"] == 8
    # c2/lab-y(밖) 가 c2/lab-y/weights 의 상위라 전체 합계에서는 weights 가 하위다
    assert (whole["summed_target_count"], whole["nested_target_count"]) == (7, 1)
    assert whole["total_bytes"]["sum"] == 50 + 500 + 100 + 300 + 200 + 700 + 1000

    def filtered(**p):
        r = uw.client.get(TOTALS, params={"storage": NFS, **p})
        assert r.status_code == 200, r.text
        body = r.json()
        assert list(body) == _TOTAL_KEYS + ["filters"] and body["filesystem"] is None
        return body
    c2 = filtered(cluster="dmsk8s2")
    # 걸러진 집합 안에서 최상위 -- 필터 밖 상위(c2/lab-y)가 빠지면 weights 가 최상위로 센다
    assert (c2["target_count"], c2["summed_target_count"], c2["nested_target_count"]) == (1, 1, 0)
    assert c2["total_bytes"] == {"sum": 900, "known": 1, "unknown": 0}
    assert c2["filters"] == {**_NO_FILTERS, "cluster": "dmsk8s2"}
    assert c2["truncated"] is False
    # path 는 받지 않는다(합계는 경로 검색과 무관)
    with_path = filtered(cluster="dmsk8s2", path="nothing-matches-this")
    assert (with_path["target_count"], with_path["total_bytes"]) == (1, c2["total_bytes"])
    alice = filtered(requester="alice")
    assert (alice["target_count"], alice["total_bytes"]["sum"]) == (2, 550)
    outside = filtered(scope="outside")
    assert (outside["summed_target_count"], outside["total_bytes"]["sum"]) == (3, 1500)
    none = filtered(user="nobody")
    assert (none["target_count"], none["total_bytes"]) == (0, {"sum": None, "known": 0, "unknown": 0})


def test_filtered_storage_totals_tell_the_window(uw, monkeypatch):
    from dms.api import routes_usage
    monkeypatch.setattr(routes_usage, "_WINDOW_LIMIT", 3)
    pv = uw.client.get(TOTALS, params={"storage": NFS, "scope": "pv"}).json()
    assert (pv["truncated"], pv["target_count"]) == (True, 1)                 # 판정 창이 넘쳤다(부분 합)
    mason = uw.client.get(TOTALS, params={"storage": NFS, "requester": "mason"}).json()
    assert (mason["truncated"], mason["target_count"]) == (True, 3)           # 맞는 대상(6)이 창을 넘었다
    monkeypatch.setattr(routes_usage, "_WINDOW_LIMIT", 6)
    assert uw.client.get(TOTALS, params={"storage": NFS, "requester": "mason"}).json()["truncated"] is False


def test_new_endpoints_keep_every_id_list_within_the_real_chunk(tmp_path, monkeypatch):
    # 실제 상수(500)로 1,100 대상: 기록 블록 조회(target IN)까지 한 문장의 자리표시자가 500 을 넘지 않는다(widest == 500).
    # 미등록 이름이라 소속은 모두 PV 주소 scan 기록이다(인벤토리가 덮지 않는 대상만 기록을 묻는다 -- 1,100 개 전부).
    import re
    from dms.db import dump_json
    art = tmp_path / "artifacts"
    client = _client(tmp_path, art)
    _login(client, "admin", "admin")
    repos = client.app.state.repos
    _bulk_jobs(repos.db, 1100, reports_for=0, art=art)
    rows = repos.db.query("SELECT request_id, target FROM data_jobs ORDER BY target")
    with repos.db.transaction():
        for i, r in enumerate(rows):
            pv = _pvb("dmsk8s", "ns", f"p{i}", None, "sc", r["target"])
            repos.db.execute(
                """INSERT INTO requests (request_id, commit_order, operation, requester_id, actor, resource_key, priority,
                                         payload, state, created_at, updated_at, target_label)
                   VALUES (:r, :o, 'scan', 'alice', 'alice', :k, 'mid', :p, 'Succeeded', :at, :at, 'label')""",
                {"r": r["request_id"], "o": i + 1, "k": f"k{i}", "p": dump_json({"pv": pv}), "at": "2026-09-01T00:00:00Z"})
    widest = []
    real_query = repos.db.query

    def query(sql, params=None):
        widest.append(max(len(set(re.findall(rf":{p}\d+", sql))) for p in ("j", "t", "d")))
        return real_query(sql, params)
    monkeypatch.setattr(repos.db, "query", query)
    body = client.get(TARGETS + "/export", params={"storage": "s1", "scope": "pv"}).json()
    assert body["count"] == body["matched_count"] == 1100 and body["truncated"] is False
    assert {r["pv"]["source"] for r in body["rows"]} == {"recorded"}
    page = client.get(TARGETS, params={"storage": "s1", "namespace": "ns", "sort": "hot", "limit": 200}).json()
    assert len(page["rows"]) == 200 and page["matched_count"] == 1100
    f = client.get(FACETS, params={"storage": "s1"}).json()
    assert f["scope"]["recorded"] == 1100 and f["requesters"] == [{"requester_id": "alice", "target_count": 1100}]
    assert client.get(TOTALS, params={"storage": "s1", "scope": "pv"}).json()["target_count"] == 1100
    assert max(widest) == 500


# ---- 2026-10-29: 검색(q) -- 경로·PV 이름(PVC 이름), 토큰 AND·부분 일치·대소문자 무시(작업 목록 검색과 같은 뜻) ----
# 사용자 요청: 「경로로 검색할 뿐 아니라, pv 이름도 검색할 수 있게 하고 일부 단어만 포함해도 뜨도록」. 위 테스트베드 모양 세계에서
# onefs·gpfs 대상의 경로엔 PVC 이름(kraken-kv-cache·models)이 없다 -- PV 이름으로만 걸리는 자리다.

def test_q_matches_each_token_in_the_path_or_a_pv_name(uw):
    get = lambda storage=NFS, **p: _names(uw, storage, **p)   # noqa: E731
    assert get(q="proof") == [t for t in NFS_DESC if "proof" in t]                     # 경로만
    assert get(ONEFS, q="kraken") == [ONEFS_ROOT + "/dms-proof"]                        # PV 이름만(경로에 없다)
    assert get(GPFS, q="models") == [GPFS_B + "/run", GPFS_A + "/dms-proof"]            # 같은 PVC 이름, 두 namespace
    # 섞인 토큰 AND -- 토큰마다 경로든 PV 이름이든, 순서·대소문자 무관
    assert get(GPFS, q="models run") == get(GPFS, q="RUN Models") == [GPFS_B + "/run"]
    assert get(GPFS, q="models plain") == []
    assert get(GPFS, q="plain") == ["plain/dir"]                                        # PV 밖 경로는 경로로
    assert get(ONEFS, q="KV-Cache dms-PROOF") == [ONEFS_ROOT + "/dms-proof"]
    assert get(q="atase") == ["team-a/datasets/proof-ov", "team-a/datasets/proof-scan",   # 단어 일부
                              "team-a/datasets/dms-proof"]
    assert get(q="weights") == ["c2/lab-y/weights"] and get(q="lab-y") == ["c2/lab-y", "c2/lab-y/weights"]
    # namespace·클러스터·StorageClass·volume 은 검색하지 않는다(그건 필터다)
    assert get(ONEFS, q="team-a") == get(ONEFS, q="dmsk8s") == get(ONEFS, q="tb-onefs") == []
    assert get(GPFS, q="models→run") == [GPFS_B + "/run"]                             # 화살표는 구분자(search_tokens)
    # 빈 값·공백뿐 = 검색 없음(메아리는 원문, 빈 문자열은 null -- path 와 같다)
    assert get(q="") == get(q="  \t ") == NFS_DESC
    assert _page(uw, q="")["filters"]["q"] is None and _page(uw, q="  \t ")["filters"]["q"] == "  \t "
    assert _page(uw, q="Models  Run")["filters"] == {"storage": NFS, "path": None, "q": "Models  Run", **_NO_FILTERS}
    # 행의 PV 이름 = 판정한 정체의 pvc(표의 「PV 이름」 열이 쓰는 값)
    [row] = _page(uw, ONEFS, q="kraken")["rows"]
    assert [v["pvc"] for v in row["pv"]["volumes"]] == ["kraken-kv-cache"] and row["pv"]["source"] == "inventory"


def test_q_finds_recorded_pv_names_also_on_filesystem_and_unregistered_storages(uw):
    # 기록 소속(PV 주소 scan 의 얼린 pv 블록)은 스토리지 종류·등록과 무관하게 생긴다(usage_attribution.may_attribute_pv) -- 지운
    # PVC 의 이름이 경로에 없어도 그 이름으로 걸린다. filesystem·미등록이라고 SQL(경로만)로 접으면 표에 보이는 PV 이름으로 못 찾는다.
    legacy = _pvb("dmsk8s", "team-z", "legacy-cache", "pvc-lc", "tb-nfs", "proj-z")
    for storage in (NFS, CEPH, "gone-nfs"):
        _seed_scan(uw.repos, uw.db, uw.art, storage=storage, target="proj-z/run", requester="alice",
                   at="2026-10-27T00:00:00Z", pv=legacy)
        _seed_scan(uw.repos, uw.db, uw.art, storage=storage, target="old-dir/plain", requester="mason",
                   at="2026-10-27T01:00:00Z")
        assert _names(uw, storage, q="legacy") == ["proj-z/run"], storage
        assert _names(uw, storage, q="CACHE run") == ["proj-z/run"], storage
        assert _names(uw, storage, q="plain") == ["old-dir/plain"], storage
        [row] = _page(uw, storage, q="legacy")["rows"]
        assert (row["pv"]["source"], row["pv"]["volumes"][0]["pvc"]) == ("recorded", "legacy-cache")
    # 클러스터를 지워 인벤토리에서 사라진 PVC 도 기록으로 산다. 2026-10-29 리뷰: 예전 단언(q="weights" → c2/lab-y/weights)은 PVC
    # 이름이 경로 끝 성분이라 경로 일치만으로 초록이었다 -- PV 이름이 경로에 없는 onefs(kraken-kv-cache)로 PV 이름 판정을 본다.
    assert _page(uw, ONEFS, q="kraken")["rows"][0]["pv"]["source"] == "inventory"
    uw.repos.k8s.delete_cluster("dmsk8s2", actor="ops")
    uw.repos.k8s.delete_cluster("dmsk8s", actor="ops")
    [row] = _page(uw, ONEFS, q="KRAKEN cache")["rows"]
    assert row["target"] == ONEFS_ROOT + "/dms-proof" and "kraken" not in row["target"]
    assert (row["pv"]["source"], row["pv"]["volumes"][0]["pvc"]) == ("recorded", "kraken-kv-cache")
    [row] = _page(uw, q="weights")["rows"]
    assert (row["target"], row["pv"]["source"], row["pv"]["volumes"][0]["pvc"]) == \
        ("c2/lab-y/weights", "recorded", "weights")


def test_q_is_sql_exact_without_possible_pv_names_and_judged_in_the_pv_window_otherwise(uw, monkeypatch):
    from dms.api import routes_usage
    calls = []
    _spy(monkeypatch, uw.client.app.state.repos.data_jobs, "scan_target_heads", calls)
    monkeypatch.setattr(routes_usage, "_WINDOW_LIMIT", 1)
    # filesystem(PV 주소 scan 기록 없음): 경로만이라 SQL 이 LIMIT 앞에서 정확히 건다 -- 창이 1 이어도 맞는 수가 정확하다
    body = _page(uw, CEPH, q="PROJ")
    assert [r["target"] for r in body["rows"]] == ["proj/y", "proj/x"]
    assert (body["matched_count"], body["window_truncated"]) == (2, False)
    assert [c[1]["tokens"] for c in calls] == [["PROJ"]]
    assert _names(uw, CEPH, q="proj X") == ["proj/x"]
    # PV 축 필터와 함께여도 PV 이름이 생길 수 없으면 토큰은 SQL 에 실린다(판정 창에 들어오는 것부터 검색에 맞는 것)
    calls.clear()
    monkeypatch.setattr(routes_usage, "_WINDOW_LIMIT", 10000)
    assert _names(uw, CEPH, q="x", scope="outside") == ["proj/x"]
    assert [c[1]["tokens"] for c in calls] == [["x"]]
    # PV 를 담는 스토리지: 토큰이 PV 이름에만 맞을 수 있어 SQL 에 싣지 않고, PV 축 필터와 같은 판정 창(최근 스캔 순)에서 거른다
    calls.clear()
    monkeypatch.setattr(routes_usage, "_WINDOW_LIMIT", 3)
    body = _page(uw, q="proof")
    # 창이 넘치면 창 밖의 경로 일치(모든 토큰이 경로에)를 SQL 로 더한다(2026-10-29 리뷰 -- 아래 test_q_finds_path_hits_outside…)
    assert [c[1]["tokens"] for c in calls] == [None, ["proof"]]
    assert (body["window_truncated"], body["matched_count"], body["window_limit"]) == (True, None, 3)   # 넘침 = 모름
    assert [r["target"] for r in body["rows"]] == [t for t in NFS_DESC if "proof" in t]
    calls.clear()
    monkeypatch.setattr(routes_usage, "_WINDOW_LIMIT", 10000)
    body = _page(uw, q="proof")
    assert (body["window_truncated"], body["matched_count"]) == (False, 5)
    assert [c[1]["tokens"] for c in calls] == [None]                                   # 넘치지 않으면 창 하나로 정확


def test_q_finds_path_hits_outside_the_pv_window_like_the_old_path(uw, monkeypatch):
    # 2026-10-29 리뷰: 포탈이 path 대신 q 를 보내면서, PV 를 담는 스토리지의 대상이 판정 창을 넘으면 경로 검색이 창 밖의 오래된 경로를
    # 못 찾았다(예전 path 는 SQL 의 LIMIT 앞이라 정확했다 -- 사용자 요청은 「경로로 검색할 뿐 아니라」). 이제 모든 토큰이 경로에 든
    # 대상은 창과 무관하게 찾고, 판정 창에 묶이는 것은 PV 이름이 있어야 맞는 대상뿐이다(그래서 맞는 수는 모름 -- null).
    from dms.api import routes_usage
    # 정렬 병합의 두 모서리: 창 밖 대상의 스캔 횟수가 더 많다(scans 정렬에서 창 안 대상들보다 앞 -- 옛 시각의 scan 이라 최근 스캔은
    # 그대로), 창 경계에 걸친 같은 시각 묶음(proof-d9 를 selfnl 과 같은 시각으로 -- 묶음 안은 이름순이라 proof-d9 가 창 안, selfnl 은 밖)
    _seed_scan(uw.repos, uw.db, uw.art, storage=NFS, target="team-a/datasets/dms-proof", requester="alice",
               at="2026-10-20T00:00:00Z")
    uw.db.execute("UPDATE data_jobs SET updated_at = :t WHERE job_id = :j",
                  {"t": "2026-10-26T02:00:00Z", "j": uw.jobs[(NFS, "proof-d9/alice/latest", "mason")]})
    monkeypatch.setattr(routes_usage, "_WINDOW_LIMIT", 3)    # 창 = c2/lab-y, proof-ov, proof-d9
    rows = lambda storage=NFS, **p: [r["target"] for r in _page(uw, storage, **p)["rows"]]   # noqa: E731
    old, new = _page(uw, path="team-d"), _page(uw, q="team-d")
    assert [r["target"] for r in old["rows"]] == [r["target"] for r in new["rows"]] == ["team-d/old/run"]
    assert (new["matched_count"], new["window_truncated"]) == (None, True)
    assert (new["rows"][0]["pv"]["source"], new["rows"][0]["pv"]["volumes"][0]["pvc"]) == ("recorded", "old")
    # PV 이름으로만 맞는 대상이 없는 검색이면 결과·순서가 예전 path 와 같다 -- 정렬 다섯 × 방향 × 상한(지표 정렬은 예전처럼 맞는
    # 대상 중 최근 창 안에서)
    for sort in ("last_scan", "scans", "bytes", "files", "hot"):
        for order in ("desc", "asc"):
            for limit in (100, 2):
                assert rows(q="PROOF", sort=sort, order=order, limit=limit) == \
                    rows(path="proof", sort=sort, order=order, limit=limit), (sort, order, limit)
    proof = ["team-a/datasets/proof-ov", "proof-d9/alice/latest", "proof-nl/a/selfnl", "team-a/datasets/proof-scan",
             "team-a/datasets/dms-proof"]
    assert rows(q="proof") == proof
    assert rows(q="proof", order="asc") == ["team-a/datasets/dms-proof", "team-a/datasets/proof-scan",
                                            "proof-d9/alice/latest", "proof-nl/a/selfnl", "team-a/datasets/proof-ov"]
    assert rows(q="proof", sort="scans") == ["team-a/datasets/dms-proof"] + proof[:4]
    assert rows(q="proof", sort="scans", order="asc") == proof[:4] + ["team-a/datasets/dms-proof"]
    # PV 축 필터와 함께: 경로 일치는 경로 일치끼리의 최근 창(예전 「path + PV 필터」의 창)에서 소속을 판정해 거른다 -- 예전과 같다
    for q in ("proof-scan", "proof", "dms-proof datasets"):
        for p in ({"namespace": "team-a"}, {"scope": "pv"}, {"scope": "outside"}, {"pvc": "datasets", "sort": "bytes"}):
            assert rows(q=q, **p) == rows(path=q.split()[0], **p), (q, p)
    [row] = _page(uw, q="proof-scan", namespace="team-a")["rows"]
    assert row["pv"]["volumes"][0]["pvc"] == "datasets"                                # 창 밖 행의 소속도 판정해 싣는다
    # 판정 창에 묶이는 것은 PV 이름이 있어야 맞는 대상뿐(알려진 한계 -- 포탈이 말한다). gpfs 의 창 2 = plain/dir, B/run
    monkeypatch.setattr(routes_usage, "_WINDOW_LIMIT", 2)
    assert rows(GPFS, q="models") == [GPFS_B + "/run"]                                 # 창 밖 A/dms-proof 는 못 찾는다
    assert rows(GPFS, q="models proof") == []                                          # 토큰 하나가 PV 이름에만 -- 창 안만
    assert rows(GPFS, q="PROOF") == [GPFS_A + "/dms-proof"]                            # 경로로는 창 밖도
    # 내보내기도 같은 판정(상한 = 창 3 -- 넘었다)
    monkeypatch.setattr(routes_usage, "_WINDOW_LIMIT", 3)
    parts, err = _drain_targets_export(uw.client, q="proof")
    assert err is None and b'"truncated":true,"capped":true,"matched_count":null,' in parts[0]   # 경로 일치 5 > 상한 3
    assert [r["target"] for r in json.loads(b"".join(parts))["rows"]] == proof[:3]


def test_q_combines_with_path_requester_and_pv_filters(uw):
    get = lambda storage=NFS, **p: _names(uw, storage, **p)   # noqa: E731
    assert get(path="team-a", q="SCAN") == ["team-a/datasets/proof-scan"]
    assert get(path="proof", q="datasets") == ["team-a/datasets/proof-ov", "team-a/datasets/proof-scan",
                                               "team-a/datasets/dms-proof"]
    assert get(path="c2", q="weights") == ["c2/lab-y/weights"]
    assert get(namespace="team-a", q="ov") == ["team-a/datasets/proof-ov"]
    assert get(scope="outside", q="proof") == ["proof-nl/a/selfnl", "proof-d9/alice/latest"]
    assert get(scope="outside", q="datasets") == []
    assert get(requester="alice", q="old") == ["team-d/old/run"]
    assert get(requester="mason", q="datasets") == ["team-a/datasets/proof-ov", "team-a/datasets/proof-scan"]
    assert get(GPFS, namespace="team-b", q="models") == [GPFS_B + "/run"]
    assert get(GPFS, q="models", sort="bytes") == [GPFS_B + "/run", GPFS_A + "/dms-proof"]   # 지표 정렬(2 > 1)
    body = _page(uw, path="proof", q="datasets", namespace="team-a")
    assert body["filters"] == {"storage": NFS, "path": "proof", "q": "datasets", **_NO_FILTERS, "namespace": "team-a"}


def test_q_wildcards_are_literal_on_both_paths_and_bad_values_are_422(uw):
    for storage in (NFS, CEPH):
        _seed_scan(uw.repos, uw.db, uw.art, storage=storage, target="misc/100%_x", requester="mason",
                   at="2026-10-27T00:00:00Z")
    for storage in (NFS, CEPH):        # PV 판정 창(파이썬 부분 문자열)·SQL(LIKE ESCAPE) 두 경로가 같은 뜻
        assert _names(uw, storage, q="%") == ["misc/100%_x"], storage
        assert _names(uw, storage, q="0%_") == ["misc/100%_x"], storage
        assert _names(uw, storage, q="1_0") == [] and _names(uw, storage, q="pro_") == [], storage
        assert _names(uw, storage, q="\\") == [], storage
    for url in (TARGETS, TARGETS + "/export"):
        assert uw.client.get(url, params={"storage": NFS, "q": "a\x00b"}).status_code == 422, url
        assert uw.client.get(url, params={"storage": NFS, "q": "q" * 513}).status_code == 422, url
        assert uw.client.get(url, params={"storage": NFS, "q": "q" * 512}).status_code == 200, url
        assert uw.client.get(url, params={"storage": NFS, "q": "a\tb\x01"}).status_code == 200, url


def test_targets_export_takes_q_with_the_same_judgement_and_echo(uw, monkeypatch):
    parts, err = _drain_targets_export(uw.client, storage=GPFS, q="Models RUN")
    body = json.loads(b"".join(parts))
    assert err is None and [r["target"] for r in body["rows"]] == [GPFS_B + "/run"]
    assert (body["matched_count"], body["count"], body["truncated"]) == (1, 1, False)
    assert body["filters"] == {"storage": GPFS, "path": None, "q": "Models RUN", **_NO_FILTERS}
    assert body["rows"][0]["pv"]["volumes"][0]["pvc"] == "models"
    r = uw.client.get(TARGETS + "/export", params={"storage": ONEFS, "q": "kraken"})
    assert [x["target"] for x in r.json()["rows"]] == [ONEFS_ROOT + "/dms-proof"] and r.json()["filters"]["q"] == "kraken"
    r = uw.client.get(TARGETS + "/export", params={"storage": CEPH, "q": "PROJ y"})
    assert [x["target"] for x in r.json()["rows"]] == ["proj/y"]
    from dms.api import routes_usage
    monkeypatch.setattr(routes_usage, "_WINDOW_LIMIT", 3)
    parts, err = _drain_targets_export(uw.client, q="proof")                  # PV 판정 창이 넘쳤다 -- 맞는 수는 모름
    assert err is None and b'"truncated":true,"capped":true,"matched_count":null,' in parts[0]
    # 창 밖의 경로 일치도 더한다(상한 3 = 창) -- 최근 스캔 순 앞 3개
    assert [r["target"] for r in json.loads(b"".join(parts))["rows"]] == [t for t in NFS_DESC if "proof" in t][:3]
    monkeypatch.setattr(routes_usage, "_WINDOW_LIMIT", 1)
    parts, err = _drain_targets_export(uw.client, storage=CEPH, q="proj")     # SQL 경로 -- 맞는 수는 정확, 상한을 넘었다
    assert err is None and b'"truncated":true,"capped":true,"matched_count":2,' in parts[0] and parts[-1] == b'],"count":1}'


def test_targets_export_says_when_known_matches_were_cut_at_the_row_cap(uw, monkeypatch):
    # 2026-10-29 2차 리뷰: PV 이름까지 보는 검색은 창이 넘치면 창 밖의 경로 일치를 더한다(_with_path_hits) -- 그래서 내보내기가 상한
    # (_WINDOW_LIMIT)에서 **알려진** 일치를 자를 수 있다. 봉투가 창 넘침(truncated + matched_count null)과 같은 모양이라 포탈은 「PV 이름으로
    # 맞는 대상은 창 안에서만」만 말했고 경로 일치가 잘린 것이 안 보였다 -- 이제 capped 가 그것을 따로 말한다.
    from dms.api import routes_usage

    def export(**p):
        parts, err = _drain_targets_export(uw.client, **p)
        assert err is None
        return json.loads(b"".join(parts))

    def shape(body):
        return body["truncated"], body["capped"], body["matched_count"], body["count"]

    monkeypatch.setattr(routes_usage, "_WINDOW_LIMIT", 3)                      # 창 = c2/lab-y, proof-ov, selfnl
    assert len([t for t in NFS_DESC if "proof" in t]) == 5
    for sort in ("last_scan", "scans", "bytes", "files", "hot"):
        for order in ("desc", "asc"):
            assert shape(export(q="proof", sort=sort, order=order)) == (True, True, None, 3), (sort, order)
    assert shape(export(q="dms-proof")) == (True, False, None, 1)              # 창만 넘었다 -- 잘린 일치 없음
    # 창 안에 맞는 것이 없고 창 밖 경로 일치만 상한만큼 -- 행 수가 상한과 같아도 더 있었다(상한 + 1 개 읽어 안다)
    monkeypatch.setattr(routes_usage, "_WINDOW_LIMIT", 1)                      # 창 = c2/lab-y
    for sort in ("last_scan", "scans", "bytes"):
        assert shape(export(q="proof", sort=sort)) == (True, True, None, 1), sort
    # PV 필터와 함께면 경로 일치끼리의 창 밖은 판정하지 않았다(모름 -- 창 문구의 몫) -- 잘린 일치가 아니다
    monkeypatch.setattr(routes_usage, "_WINDOW_LIMIT", 3)
    body = export(q="proof", flt=_flt(namespace="team-a"))
    assert shape(body) == (True, False, None, 1) and [r["target"] for r in body["rows"]] == ["team-a/datasets/proof-ov"]
    # 창 넘침이 아닌 PV 경로는 잘리지 않는다(창 ≤ 상한), SQL 경로는 맞는 수가 상한을 넘을 때 -- truncated 와 같다
    assert shape(export(flt=_flt(scope="pv"))) == (True, False, None, 1)
    assert shape(export(flt=_flt(requester="mason"))) == (True, True, 6, 3)
    monkeypatch.setattr(routes_usage, "_WINDOW_LIMIT", 6)
    assert shape(export(flt=_flt(requester="mason"))) == (False, False, 6, 6)
    monkeypatch.setattr(routes_usage, "_WINDOW_LIMIT", len(NFS_DESC))          # 창이 대상 전부 -- 넘침도 잘림도 없다
    assert shape(export(q="proof")) == (False, False, 5, 5)
    # 창 안의 PV 이름 일치 + 창 밖 경로 일치가 상한을 넘는다(경로 일치끼리는 그 창 안 -- 지표 정렬은 앞 창으로 자른 것으로 안다)
    for i in (1, 2):
        _seed_scan(uw.repos, uw.db, uw.art, storage=GPFS, target=f"archive/models-{i}", requester="mason",
                   at=f"2026-10-0{i}T00:00:00Z")
    monkeypatch.setattr(routes_usage, "_WINDOW_LIMIT", 2)                      # gpfs 창 = plain/dir, B/run(PV 이름 models)
    for sort in ("last_scan", "scans", "bytes", "hot"):
        body = export(storage=GPFS, q="models", sort=sort)
        assert shape(body) == (True, True, None, 2), sort
        assert GPFS_B + "/run" in [r["target"] for r in body["rows"]], sort


def test_facets_and_storage_totals_do_not_take_q(uw):
    # 합계는 경로 검색과 무관하다(2026-10-21 결정) -- q 를 실어도 모르는 파라미터로 무시되고 필터 합계(메아리)가 되지 않는다.
    whole = _totals(uw.client, NFS)
    r = uw.client.get(TOTALS, params={"storage": NFS, "q": "nothing-matches"}).json()
    assert "filters" not in r and (r["target_count"], r["total_bytes"]) == (whole["target_count"], whole["total_bytes"])
    assert uw.client.get(FACETS, params={"storage": NFS, "q": "nothing-matches"}).json()["target_count"] == 8
