"""결과 파일 자동 만료의 API 계약(2026-10-26): 만료된 잡의 열람은 410 artifact_expired(목록은 빈 목록 + 만료 표시),
소유 확인이 먼저(남의 잡엔 404 -- 410 이 존재 오라클이 되지 않는다), 잡 응답에 artifacts_expired_at, 관리자 아티팩트 경로
화면에 보존일·만료 정리 현황."""
import os

import pytest

from dms.artifact_expiry import ArtifactExpirer
from dms.db import iso_plus, utc_now_iso
from dms.domain import DataJobState, RequestState

ADMIN = {"Authorization": "Bearer tok-shared"}


def _login(client, name):
    client.post("/api/auth/signup", json={"username": name, "password": "p"})
    assert client.post("/api/auth/login", json={"username": name, "password": "p"}).status_code == 200


def _finished_job(repos, db, *, requester="alice", updated_at="2000-01-01T00:00:00Z"):
    rid = repos.requests.create(operation="scan", requester_id=requester, actor=requester,
                                resource_key=f"k-{os.urandom(4).hex()}", payload={"storage": "s1", "target": "t"},
                                priority="mid")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan_id, operation="scan", priority="mid", storage_name="s1", target="t",
                                     options={}, tool="dscan", precondition={}, actor="planner",
                                     worker_pool={"identity": {"uid": os.getuid(), "gid": os.getgid(),
                                                               "username": requester}})
    repos.data_jobs.set_job_state(jid, DataJobState.SUCCEEDED, actor="stepper")
    repos.requests.finalize_from_job(rid, DataJobState.SUCCEEDED, actor="stepper")
    db.execute("UPDATE data_jobs SET updated_at = :t WHERE job_id = :j", {"t": updated_at, "j": jid})
    return rid, jid


def _expire(client, now=None):
    app = client.app.state
    return ArtifactExpirer(app.repos, settings=app.settings, clock=lambda: now or utc_now_iso()).run_once()


@pytest.fixture
def expired(client, db):
    repos = client.app.state.repos
    rid, jid = _finished_job(repos, db)
    assert _expire(client)["queued"] == 1
    return rid, jid


def test_list_of_an_expired_job_is_empty_with_the_expiry_stamp(client, expired):
    _rid, jid = expired
    _login(client, "alice")
    r = client.get(f"/api/user/jobs/{jid}/artifacts")
    assert r.status_code == 200
    body = r.json()
    stamp = client.app.state.repos.data_jobs.get_job(jid)["artifacts_expired_at"]
    assert body == {"entries": [], "truncated": False, "expired_at": stamp, "retention_days": 1500}


def test_file_view_and_download_of_an_expired_job_are_410(client, expired):
    _rid, jid = expired
    _login(client, "alice")
    for url in (f"/api/user/jobs/{jid}/artifacts/execution/stdout.log",
                f"/api/user/jobs/{jid}/artifacts/execution/stdout.log?tail=10",
                f"/api/user/jobs/{jid}/artifacts/execution/stdout.log/download"):
        r = client.get(url)
        assert (r.status_code, r.json()["detail"]) == (410, "artifact_expired"), url
    # 관리자(공유 토큰)도 같다.
    r = client.get(f"/api/user/jobs/{jid}/artifacts/execution/stdout.log", headers=ADMIN)
    assert (r.status_code, r.json()["detail"]) == (410, "artifact_expired")


def test_someone_elses_expired_job_is_still_404(client, expired):
    # 소유 확인이 먼저다 -- 410 을 주면 남의 잡 id 가 있는지(그리고 만료됐는지) 캐는 오라클이 된다.
    _rid, jid = expired
    _login(client, "bob")
    for url in (f"/api/user/jobs/{jid}/artifacts", f"/api/user/jobs/{jid}/artifacts/execution/stdout.log",
                f"/api/user/jobs/{jid}/artifacts/execution/stdout.log/download"):
        r = client.get(url)
        assert (r.status_code, r.json()["detail"]) == (404, "job_not_found"), url


def test_job_rows_carry_the_expiry_stamp_and_the_rest_of_the_detail_stays(client, expired):
    rid, jid = expired
    _login(client, "alice")
    jobs = client.get(f"/api/user/requests/{rid}/jobs").json()
    assert len(jobs) == 1 and jobs[0]["artifacts_expired_at"] is not None
    assert jobs[0]["state"] == "Succeeded" and jobs[0]["transitions"]
    assert client.get(f"/api/user/requests/{rid}").status_code == 200


def test_unexpired_job_list_shape_is_unchanged(client, db, tmp_path):
    repos = client.app.state.repos
    _rid, jid = _finished_job(repos, db, updated_at=utc_now_iso())
    assert _expire(client)["queued"] == 0
    _login(client, "alice")
    body = client.get(f"/api/user/jobs/{jid}/artifacts").json()
    assert "expired_at" not in body and "retention_days" not in body
    assert client.app.state.repos.data_jobs.get_job(jid)["artifacts_expired_at"] is None


def test_retention_days_zero_reports_none_on_an_already_expired_job(client, expired):
    import dataclasses
    _rid, jid = expired
    client.app.state.settings = dataclasses.replace(client.app.state.settings, artifact_retention_days=0)
    _login(client, "alice")
    assert client.get(f"/api/user/jobs/{jid}/artifacts").json()["retention_days"] is None


def test_artifact_base_page_shows_retention_and_expiry_backlog(client, db, expired):
    body = client.get("/api/admin/artifact-base", headers=ADMIN).json()
    ret = body["artifact_retention"]
    assert ret["days"] == 1500
    assert (ret["pending"], ret["stalled"], ret["last_error"], ret["expired_jobs"]) == (1, 0, None, 1)
    # 만료 대기 행의 잡은 살아 있다 -- 잠금 수(잡 + 삭제 정리 대기)에 두 번 세지 않는다.
    assert body["locked_by_jobs"] == 1
    db.execute("UPDATE request_purges SET last_error = 'purge_no_node'")
    ret = client.get("/api/admin/artifact-base", headers=ADMIN).json()["artifact_retention"]
    assert (ret["stalled"], ret["last_error"]) == (1, "purge_no_node")


def test_expiry_rows_do_not_show_on_the_deletion_toolbar_surface(client, expired):
    body = client.get("/api/admin/request-purges", headers=ADMIN).json()
    assert body["pending"] == 0 and body["items"] == []


def test_next_day_boundary_with_a_real_clock(client, db):
    # 정확히 N일(1500일) 전에 끝난 잡은 아직 열람된다 -- 1초 더 지나야 만료한다.
    repos = client.app.state.repos
    now = utc_now_iso()
    _rid, jid = _finished_job(repos, db, updated_at=iso_plus(now, -1500 * 86400))
    assert _expire(client, now)["queued"] == 0
    assert _expire(client, iso_plus(now, 1))["queued"] == 1
    assert repos.data_jobs.get_job(jid)["artifacts_expired_at"] == iso_plus(now, 1)
