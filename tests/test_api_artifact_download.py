"""다운로드 라우트 계약(슬라이스 26 Task 3).

핵심은 오라클 봉쇄다: open/봉쇄 실패는 뷰 라우트 404 와 **body 까지** 동일해야
하고(갈리면 봉쇄 밖 파일 존재를 캐는 오라클), 413(artifact_too_large)은 봉쇄
통과 뒤에만 나간다(순서 역전은 크기 오라클). 골격은 test_api_artifacts.py 의
_client/_login/_confirmpending_job 복붙 관례를 따른다.
"""
import os

from dms.config import Settings
from dms.db import Database
from dms.domain import DataJobState, RequestState
from dms.migrations import migrate
from fastapi.testclient import TestClient


def _client(tmp_path, artifact_base=None, db_name="artifacts.db",
            download_max_bytes=None):
    from dms.api.app import create_app
    db = Database.connect(f"sqlite:///{tmp_path}/{db_name}")
    migrate(db)
    kwargs = {}
    if artifact_base is not None:
        kwargs["artifact_base_uri"] = f"file://{artifact_base}"
    if download_max_bytes is not None:
        # 테스트에서 256MiB 파일을 만들지 않는다 — 상한을 작게 줘서 경계를 재현한다.
        kwargs["artifact_download_max_bytes"] = download_max_bytes
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


def _job_with_dir(tmp_path, download_max_bytes=None):
    art_base = tmp_path / "artifacts"
    client = _client(tmp_path, artifact_base=str(art_base),
                     download_max_bytes=download_max_bytes)
    rid, jid = _confirmpending_job(client.app.state.repos, requester="alice")
    d = art_base / jid / "execution"
    d.mkdir(parents=True)
    _login(client, "alice")
    return client, jid, d


def _dl(jid, name):
    return f"/api/user/jobs/{jid}/artifacts/execution/{name}/download"


def _view(jid, name):
    return f"/api/user/jobs/{jid}/artifacts/execution/{name}"


def test_download_streams_exact_bytes_including_binary(tmp_path):
    # NUL 포함 바이너리(16KB)가 디코드 오염 없이 바이트 단위로 완전 일치해야 한다 —
    # 뷰(errors="replace" 디코드)와 달리 다운로드는 원본 그대로가 정의다.
    client, jid, d = _job_with_dir(tmp_path)
    payload = bytes(range(256)) * 64
    (d / "dump.bin").write_bytes(payload)
    r = client.get(_dl(jid, "dump.bin"))
    assert r.status_code == 200
    assert r.content == payload
    assert r.headers["content-length"] == str(len(payload))


def test_download_headers_are_pinned(tmp_path):
    # 헤더 3종이 함께 stored-XSS 경로를 닫는다(설계 §2.2) — inline 표시 절대 금지.
    client, jid, d = _job_with_dir(tmp_path)
    (d / "stdout.log").write_text("hello")
    r = client.get(_dl(jid, "stdout.log"))
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/octet-stream"
    assert r.headers["content-disposition"] == 'attachment; filename="stdout.log"'
    assert r.headers["x-content-type-options"] == "nosniff"


def test_download_404_body_is_identical_to_view_404(tmp_path):
    # 오라클 계약: 단순 미존재든 봉쇄 실패(바깥 심링크)든, 다운로드 404 는 뷰 404 와
    # status 만이 아니라 body 까지 완전히 같아야 한다 — 갈리는 순간 라우트 간 차이가
    # 존재 오라클이 된다.
    client, jid, d = _job_with_dir(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("TOP-SECRET")
    os.symlink(outside, d / "leak.log")
    for name in ("nope.log", "leak.log"):
        dl = client.get(_dl(jid, name))
        view = client.get(_view(jid, name))
        assert "TOP-SECRET" not in dl.text
        assert dl.status_code == 404
        assert (dl.status_code, dl.json()) == (view.status_code, view.json())


def test_download_fifo_is_404(tmp_path):
    # 사용자가 자기 phase 디렉터리 소유자라 mkfifo 가 가능하다 — O_NONBLOCK open 후
    # S_ISREG 에서 걸려 404 로 뭉개져야 한다(라우트 계층 재확인).
    client, jid, d = _job_with_dir(tmp_path)
    os.mkfifo(d / "pipe.log")
    r = client.get(_dl(jid, "pipe.log"))
    assert r.status_code == 404
    assert r.json()["detail"] == "artifact_not_found"


def test_download_too_large_is_413_artifact_too_large(tmp_path):
    client, jid, d = _job_with_dir(tmp_path, download_max_bytes=1024)
    (d / "big.log").write_bytes(b"x" * 1025)
    r = client.get(_dl(jid, "big.log"))
    assert r.status_code == 413
    assert r.json()["detail"] == "artifact_too_large"


def test_download_at_cap_is_200(tmp_path):
    # 경계 off-by-one 고정: 정확히 상한 크기는 초과가 아니다.
    client, jid, d = _job_with_dir(tmp_path, download_max_bytes=1024)
    payload = b"y" * 1024
    (d / "cap.log").write_bytes(payload)
    r = client.get(_dl(jid, "cap.log"))
    assert r.status_code == 200
    assert r.content == payload


def test_zero_byte_download_is_200_empty(tmp_path):
    # 0 바이트는 정상값(빈 파일)이다 — 404 나 오류로 뭉개면 null(모름)과 0 을 섞는 것.
    client, jid, d = _job_with_dir(tmp_path)
    (d / "empty.log").write_bytes(b"")
    r = client.get(_dl(jid, "empty.log"))
    assert r.status_code == 200
    assert r.headers["content-length"] == "0"
    assert r.content == b""


def test_view_route_is_unchanged(tmp_path):
    # 설계 §5 "뷰 라우트 무변경" — 같은 파일에 뷰 GET 은 기존 JSON 계약 그대로다.
    client, jid, d = _job_with_dir(tmp_path)
    (d / "stdout.log").write_text("hello world")
    r = client.get(_view(jid, "stdout.log"))
    assert r.status_code == 200
    body = r.json()
    assert body["content"] == "hello world"
    assert body["truncated"] is False


def test_not_owner_is_404(tmp_path):
    # _owned_job 재사용 확인 — 타 사용자에겐 잡 자체가 없는 것과 같아야 한다.
    art_base = tmp_path / "artifacts"
    client = _client(tmp_path, artifact_base=str(art_base))
    rid, jid = _confirmpending_job(client.app.state.repos, requester="alice")
    d = art_base / jid / "execution"
    d.mkdir(parents=True)
    (d / "stdout.log").write_text("hello")
    _login(client, "eve")
    r = client.get(_dl(jid, "stdout.log"))
    assert r.status_code == 404
    assert r.json()["detail"] == "job_not_found"
