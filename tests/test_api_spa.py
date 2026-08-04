from dms.db import Database
from dms.migrations import migrate
from dms.config import Settings
from dms.api.app import create_app
from fastapi.testclient import TestClient


def _client(tmp_path, static_dir):
    db = Database.connect(f"sqlite:///{tmp_path}/spa.db")
    migrate(db)
    settings = Settings(database_url="unused", shared_token="t", admin_token="a",
                        session_secret="s", static_dir=str(static_dir))
    return TestClient(create_app(settings, db))


def test_spa_root_serves_index(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>DMS</title>")
    client = _client(tmp_path, dist)
    r = client.get("/")
    assert r.status_code == 200
    assert "DMS" in r.text


def test_spa_unknown_path_falls_back_to_index(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("INDEX")
    client = _client(tmp_path, dist)
    r = client.get("/admin/dashboard")     # 클라이언트 라우트, 서버엔 없음
    assert r.status_code == 200
    assert r.text == "INDEX"


def test_api_route_not_shadowed_by_spa(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("INDEX")
    client = _client(tmp_path, dist)
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_no_static_dir_means_no_mount(tmp_path):
    db = Database.connect(f"sqlite:///{tmp_path}/nomount.db")
    migrate(db)
    settings = Settings(database_url="unused", shared_token="t", admin_token="a",
                        session_secret="s")   # static_dir 미지정
    client = TestClient(create_app(settings, db))
    assert client.get("/").status_code == 404   # 마운트 없음 → 404


def test_spa_path_traversal_falls_back_to_index_not_sibling_file(tmp_path):
    # static_dir의 형제 디렉터리로 이탈을 시도한다. 리터럴 ".."는 HTTP 클라이언트가
    # 요청 전에 정규화해버리므로, 서버까지 그대로 전달되도록 %2e%2e로 인코딩한다.
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("INDEX")
    secret_dir = tmp_path / "dist-secret"     # "dist"로 시작하는 형제 디렉터리
    secret_dir.mkdir()
    (secret_dir / "secret.txt").write_text("TOP-SECRET")
    client = _client(tmp_path, dist)
    r = client.get("/%2e%2e/dist-secret/secret.txt")
    assert r.status_code == 200
    assert r.text == "INDEX"
    assert "TOP-SECRET" not in r.text


def test_spa_serves_real_file_at_root_with_relative_static_dir(tmp_path, monkeypatch):
    # static_dir가 상대경로여도 static_dir 루트의 실재 파일은 정상 서빙되어야 한다.
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("INDEX")
    (dist / "favicon.ico").write_text("ICON-BYTES")
    monkeypatch.chdir(tmp_path)
    db = Database.connect(f"sqlite:///{tmp_path}/spa-relative.db")
    migrate(db)
    settings = Settings(database_url="unused", shared_token="t", admin_token="a",
                        session_secret="s", static_dir="dist")   # 상대경로
    client = TestClient(create_app(settings, db))
    r = client.get("/favicon.ico")
    assert r.status_code == 200
    assert r.text == "ICON-BYTES"
