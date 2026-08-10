import os

from dms.config import Settings
from dms.db import Database
from dms.domain import DataJobState, RequestState
from dms.migrations import migrate
from fastapi.testclient import TestClient


def _client(tmp_path, artifact_base=None, db_name="artifacts.db"):
    from dms.api.app import create_app
    db = Database.connect(f"sqlite:///{tmp_path}/{db_name}")
    migrate(db)
    kwargs = {}
    if artifact_base is not None:
        kwargs["artifact_base_uri"] = f"file://{artifact_base}"
    settings = Settings(database_url="unused", shared_token="tok-shared",
                        admin_token="tok-admin", session_secret="sess-secret", **kwargs)
    return TestClient(create_app(settings, db))


def _login(client, name="alice"):
    client.post("/api/auth/signup", json={"username": name, "password": "p"})
    client.post("/api/auth/login", json={"username": name, "password": "p"})


def _confirmpending_job(app_repos, requester="alice"):
    repos = app_repos
    rid = repos.requests.create(operation="sync", requester_id=requester, actor=requester,
        resource_key="k", payload={"source_storage": "src", "source": "a",
        "destination_storage": "dst", "destination": "b"}, priority="mid")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    repos.requests.set_state(rid, RequestState.RUNNING, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan_id, operation="sync", priority="mid",
        source_storage="src", source="a", destination_storage="dst", destination="b",
        options={}, tool="dsync", worker_pool={}, precondition={}, actor="planner")
    repos.data_jobs.set_preview(jid, fingerprint="sha256:abc",
        expires_at="2099-01-01T00:00:00Z", artifact_uri="file:///art/j")
    repos.data_jobs.set_job_state(jid, DataJobState.CONFIRM_PENDING, actor="stepper")
    return rid, jid


ADMIN = {"Authorization": "Bearer tok-shared"}


def test_list_returns_phase_name_size(tmp_path):
    art_base = tmp_path / "artifacts"
    client = _client(tmp_path, artifact_base=str(art_base))
    repos = client.app.state.repos
    rid, jid = _confirmpending_job(repos, requester="alice")
    d = art_base / jid / "execution"
    d.mkdir(parents=True)
    (d / "stdout.log").write_text("hello")
    _login(client, "alice")
    r = client.get(f"/api/user/jobs/{jid}/artifacts")
    assert r.status_code == 200
    body = r.json()
    assert body["truncated"] is False
    rows = body["entries"]
    assert len(rows) == 1
    assert rows[0]["phase"] == "execution"
    assert rows[0]["name"] == "stdout.log"
    assert rows[0]["size"] == 5


def test_get_artifact_returns_content(tmp_path):
    art_base = tmp_path / "artifacts"
    client = _client(tmp_path, artifact_base=str(art_base))
    repos = client.app.state.repos
    rid, jid = _confirmpending_job(repos, requester="alice")
    d = art_base / jid / "execution"
    d.mkdir(parents=True)
    (d / "stdout.log").write_text("hello world")
    _login(client, "alice")
    r = client.get(f"/api/user/jobs/{jid}/artifacts/execution/stdout.log")
    assert r.status_code == 200
    body = r.json()
    assert body["content"] == "hello world"
    assert body["truncated"] is False


def test_invalid_phase_422(tmp_path):
    art_base = tmp_path / "artifacts"
    client = _client(tmp_path, artifact_base=str(art_base))
    repos = client.app.state.repos
    rid, jid = _confirmpending_job(repos, requester="alice")
    _login(client, "alice")
    r = client.get(f"/api/user/jobs/{jid}/artifacts/bogus-phase/stdout.log")
    assert r.status_code == 422
    assert r.json()["detail"] == "invalid_phase"


def test_name_with_slash_never_returns_file_content(tmp_path):
    # 라우트가 /{phase}/{name} 2개의 경로 세그먼트만 받으므로, %2f로 인코딩된
    # 슬래시가 포함된 name은 스타레트 라우팅 단계에서부터 매칭 실패한다 — 우리
    # 헬퍼의 invalid_artifact_name(422)이 아니라 스타레트 기본 404 "Not Found"가
    # 나온다(핸들러에 도달하지 못함, 실제로 확인함). 정확한 코드를 단언하되,
    # 파일 내용이 응답에 나타나지 않는 것만큼은 항상 단언한다.
    art_base = tmp_path / "artifacts"
    client = _client(tmp_path, artifact_base=str(art_base))
    repos = client.app.state.repos
    rid, jid = _confirmpending_job(repos, requester="alice")
    d = art_base / jid / "execution"
    d.mkdir(parents=True)
    (d / "stdout.log").write_text("SECRET-CONTENT")
    _login(client, "alice")
    r = client.get(f"/api/user/jobs/{jid}/artifacts/execution/sub%2Fstdout.log")
    assert "SECRET-CONTENT" not in r.text
    assert r.status_code == 404


def test_dotdot_attempt_never_returns_file_outside_base(tmp_path):
    # 인코딩된 "..%2f..%2f"는 서버 라우팅 단계에서 %2f가 리터럴 "/"로 디코딩되어
    # {phase}/{name} 2세그먼트 라우트와 매칭되지 않는다 — 핸들러(및 헬퍼)에는
    # 도달조차 하지 않는다. 리터럴 ".."는 확인해 보니(별도 프로브 스크립트)
    # httpx가 요청 전에 URL을 정규화해 "execution/.."을 지워버려 목록 엔드포인트
    # 호출로 바뀐다(test_api_spa.py의 동일 현상과 같음) — 어느 경로로도 파일
    # 내용은 응답에 나타나지 않는다.
    art_base = tmp_path / "artifacts"
    client = _client(tmp_path, artifact_base=str(art_base))
    repos = client.app.state.repos
    rid, jid = _confirmpending_job(repos, requester="alice")
    outside = tmp_path / "outside.txt"
    outside.write_text("TOP-SECRET")
    d = art_base / jid / "execution"
    d.mkdir(parents=True)
    _login(client, "alice")
    r = client.get(f"/api/user/jobs/{jid}/artifacts/execution/..%2f..%2foutside.txt")
    assert "TOP-SECRET" not in r.text
    assert r.status_code == 404


def test_other_users_job_404_admin_can_access(tmp_path):
    art_base = tmp_path / "artifacts"
    client = _client(tmp_path, artifact_base=str(art_base))
    repos = client.app.state.repos
    rid, jid = _confirmpending_job(repos, requester="alice")
    d = art_base / jid / "execution"
    d.mkdir(parents=True)
    (d / "stdout.log").write_text("hello")
    _login(client, "eve")
    r = client.get(f"/api/user/jobs/{jid}/artifacts")
    assert r.status_code == 404
    assert r.json()["detail"] == "job_not_found"
    r2 = client.get(f"/api/user/jobs/{jid}/artifacts", headers=ADMIN)
    assert r2.status_code == 200
    assert len(r2.json()["entries"]) == 1


def test_missing_artifact_base_dir_list_empty_body_404(tmp_path):
    art_base = tmp_path / "does-not-exist"
    client = _client(tmp_path, artifact_base=str(art_base))
    repos = client.app.state.repos
    rid, jid = _confirmpending_job(repos, requester="alice")
    _login(client, "alice")
    r = client.get(f"/api/user/jobs/{jid}/artifacts")
    assert r.status_code == 200
    assert r.json() == {"entries": [], "truncated": False}
    r2 = client.get(f"/api/user/jobs/{jid}/artifacts/execution/stdout.log")
    assert r2.status_code == 404


# --- 보안 회귀 테스트 (슬라이스 5 Task 2 리뷰) -------------------------------
# 기존 "공격" 테스트들은 스타레트 라우터에서 404로 끝나 핸들러에 도달조차 하지
# 않았다(아무것도 증명하지 못했다). 아래 테스트들은 핸들러까지 실제로 도달한다.

def _job_with_artifacts(tmp_path):
    art_base = tmp_path / "artifacts"
    client = _client(tmp_path, artifact_base=str(art_base))
    rid, jid = _confirmpending_job(client.app.state.repos, requester="alice")
    d = art_base / jid / "execution"
    d.mkdir(parents=True)
    _login(client, "alice")
    return client, jid, art_base, d


def test_literal_dotdot_name_reaches_handler_and_is_422(tmp_path):
    # httpx는 리터럴 ".."를 클라이언트 쪽에서 정규화해 없애 버린다 — "%2e%2e"는
    # 정규화되지 않고 서버까지 가서 name=".."로 디코딩된다(핸들러 도달을 보장).
    client, jid, _, _ = _job_with_artifacts(tmp_path)
    r = client.request(
        "GET", f"http://testserver/api/user/jobs/{jid}/artifacts/execution/%2e%2e")
    assert r.status_code == 422
    assert r.json()["detail"] == "invalid_artifact_name"


def test_dotdot_with_trailing_newline_is_rejected(tmp_path):
    # "..\n" — re의 '$'가 끝 개행 앞에서 매칭돼 점-전용 가드를 우회하던 케이스.
    client, jid, _, _ = _job_with_artifacts(tmp_path)
    r = client.request(
        "GET", f"http://testserver/api/user/jobs/{jid}/artifacts/execution/%2e%2e%0a")
    assert r.status_code == 422
    assert r.json()["detail"] == "invalid_artifact_name"


def test_symlink_to_outside_file_is_indistinguishable_from_missing(tmp_path):
    client, jid, art_base, d = _job_with_artifacts(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("TOP-SECRET")
    os.symlink(outside, d / "stdout.log")
    r = client.get(f"/api/user/jobs/{jid}/artifacts/execution/stdout.log")
    missing = client.get(f"/api/user/jobs/{jid}/artifacts/execution/nope.log")
    assert "TOP-SECRET" not in r.text
    assert r.status_code == 404
    assert (r.status_code, r.json()) == (missing.status_code, missing.json())


def test_symlink_existence_oracle_is_closed(tmp_path):
    # 존재하는 바깥 파일로의 심링크(예전 403)와 존재하지 않는 경로로의 심링크(404)가
    # 구별되면 팟 안의 임의 절대경로 존재 여부를 캐낼 수 있다.
    client, jid, art_base, d = _job_with_artifacts(tmp_path)
    existing = tmp_path / "exists.txt"
    existing.write_text("x")
    os.symlink(existing, d / "a.log")
    os.symlink(tmp_path / "no-such-file", d / "b.log")
    ra = client.get(f"/api/user/jobs/{jid}/artifacts/execution/a.log")
    rb = client.get(f"/api/user/jobs/{jid}/artifacts/execution/b.log")
    assert (ra.status_code, ra.json()) == (rb.status_code, rb.json())
    assert ra.status_code == 404


def test_list_does_not_follow_symlinks(tmp_path):
    client, jid, art_base, d = _job_with_artifacts(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("TOP-SECRET")
    os.symlink(outside, d / "stdout.log")
    outside_dir = tmp_path / "outside-dir"
    outside_dir.mkdir()
    (outside_dir / "leaked.conf").write_text("x")
    os.symlink(outside_dir, art_base / jid / "preflight")
    (d / "real.log").write_text("ok")
    r = client.get(f"/api/user/jobs/{jid}/artifacts")
    assert r.status_code == 200
    names = [row["name"] for row in r.json()["entries"]]
    assert names == ["real.log"]


def test_read_response_never_exceeds_max_bytes(tmp_path):
    from dms.api.artifacts import MAX_BYTES

    client, jid, _, d = _job_with_artifacts(tmp_path)
    (d / "stdout.log").write_bytes(b"C" * (MAX_BYTES * 3) + b"TAIL")
    r = client.get(f"/api/user/jobs/{jid}/artifacts/execution/stdout.log")
    assert r.status_code == 200
    body = r.json()
    assert len(body["content"].encode()) <= MAX_BYTES
    assert body["truncated"] is True


def test_unreadable_file_is_404_not_500(tmp_path):
    client, jid, _, d = _job_with_artifacts(tmp_path)
    f = d / "stdout.log"
    f.write_text("hello")
    os.chmod(f, 0o000)
    try:
        r = client.get(f"/api/user/jobs/{jid}/artifacts/execution/stdout.log")
        assert r.status_code == 404
        assert r.json()["detail"] == "artifact_not_found"
    finally:
        os.chmod(f, 0o600)


def test_list_is_capped_and_marked_truncated(tmp_path):
    from dms.api.artifacts import MAX_ENTRIES

    client, jid, _, d = _job_with_artifacts(tmp_path)
    for i in range(MAX_ENTRIES + 50):
        (d / f"f{i:05d}.log").write_text("x")
    r = client.get(f"/api/user/jobs/{jid}/artifacts")
    assert r.status_code == 200
    body = r.json()
    assert len(body["entries"]) == MAX_ENTRIES
    assert body["truncated"] is True
