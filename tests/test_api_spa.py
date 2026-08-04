from pathlib import Path
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
