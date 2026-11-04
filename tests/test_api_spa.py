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


def _dist_with_assets(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("INDEX")
    (dist / "assets" / "app-abc.js").write_text("JS-BYTES")
    return dist


def _is_json_not_html(r):
    return (r.headers["content-type"].startswith("application/json")
            and "INDEX" not in r.text)


# 2026-11-04 기능 점검 결함 #1: catch-all GET 이 /api 를 FULL 로 잡아 없는 /api 경로·다른 메서드 전용 경로의 GET 이
# 200 index.html 이 됐다. /api·/api/* 는 SPA 폴백 매치 밖 -- FastAPI 기본 JSON 404/405 로 간다(프런트 request() 는
# 문자열 detail 을 코드로 쓴다).
def test_missing_api_get_is_json_404_not_index(tmp_path):
    client = _client(tmp_path, _dist_with_assets(tmp_path))
    for path in ("/api/does-not-exist", "/api/admin/denylist", "/api/user/no/such/thing", "/api", "/api/"):
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 404, path
        assert _is_json_not_html(r), path
        assert r.json() == {"detail": "Not Found"}, path
    # 인증 헤더가 있어도 같다(라우트가 없으니 인증까지 가지 않는다).
    r = client.get("/api/admin/no-such", headers={"Authorization": "Bearer t"})
    assert r.status_code == 404 and _is_json_not_html(r)


def test_get_on_other_method_only_api_route_is_json_405_with_allow(tmp_path):
    client = _client(tmp_path, _dist_with_assets(tmp_path))
    r = client.get("/api/admin/k8s/settings")                 # PUT 전용
    assert r.status_code == 405
    assert _is_json_not_html(r)
    assert r.json() == {"detail": "Method Not Allowed"}
    assert r.headers["allow"] == "PUT"
    r = client.get("/api/admin/storages/some-name")           # PUT·DELETE 전용(라우트 둘)
    assert r.status_code == 405 and _is_json_not_html(r)
    # Starlette 는 첫 PARTIAL 라우트의 메서드만 Allow 에 싣는다(FastAPI 기본 동작 그대로 -- 여기서 고치지 않는다).
    assert "PUT" in r.headers["allow"]


def test_non_get_on_missing_api_path_is_404_not_405(tmp_path):
    # 예전엔 GET 전용 catch-all 의 PARTIAL 매치로 405 였다 -- 없는 경로는 메서드와 무관하게 404.
    client = _client(tmp_path, _dist_with_assets(tmp_path))
    r = client.post("/api/does-not-exist", json={})
    assert r.status_code == 404 and _is_json_not_html(r)


def test_api_trailing_slash_is_json_404_not_redirect_or_index(tmp_path):
    # redirect_slashes=False(app.py): 켜 두면 307 Location 이 scope scheme 의 절대 URL 이라 TLS 엣지 뒤에서 http:// 로 내려가고
    # 307 은 쓰기 본문을 그대로 다시 보낸다 -- 끝 슬래시 /api 는 메서드와 무관하게 다른 없는 /api 경로처럼 JSON 404.
    client = _client(tmp_path, _dist_with_assets(tmp_path))
    for method, path in (("GET", "/api/admin/storages/"), ("POST", "/api/admin/storages/"),
                         ("POST", "/api/auth/login/"), ("DELETE", "/api/admin/storages/s1/")):
        r = client.request(method, path, follow_redirects=False, headers={"Authorization": "Bearer t"})
        assert r.status_code == 404, (method, path)
        assert "location" not in r.headers, (method, path)
        assert _is_json_not_html(r) and r.json() == {"detail": "Not Found"}, (method, path)
    # 비 API 경로의 끝 슬래시는 그대로 SPA 몫이다(리다이렉트가 아니라 index).
    r = client.get("/admin/storages/", follow_redirects=False)
    assert r.status_code == 200 and r.text == "INDEX"


def test_real_api_gets_unchanged_with_spa_mounted(tmp_path):
    client = _client(tmp_path, _dist_with_assets(tmp_path))
    r = client.get("/api/admin/storages", headers={"Authorization": "Bearer t"})
    assert r.status_code == 200 and r.json() == []
    r = client.get("/api/admin/storages")                      # 익명은 진짜 라우트의 인증 거절(JSON)
    assert r.status_code == 401 and _is_json_not_html(r)
    assert client.get("/healthz").json() == {"status": "ok"}
    r = client.get("/readyz")
    assert r.status_code == 200 and r.json()["status"] == "ok"
    r = client.get("/openapi.json")
    assert r.status_code == 200 and r.json()["info"]["title"] == "dms"
    r = client.get("/docs")                                    # FastAPI 문서 화면도 SPA 폴백보다 먼저다
    assert r.status_code == 200 and "INDEX" not in r.text


def test_client_routes_and_api_lookalikes_still_serve_index(tmp_path):
    client = _client(tmp_path, _dist_with_assets(tmp_path))
    # 「api」로 시작하지만 /api 세그먼트가 아닌 경로는 SPA 몫이다(접두 문자열이 아니라 세그먼트로 가른다).
    for path in ("/admin/storages", "/jobs/0123456789abcdef", "/apiary", "/api-tokens", "/admin/api"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert r.text == "INDEX", path


def test_static_assets_unchanged_with_api_exclusion(tmp_path):
    client = _client(tmp_path, _dist_with_assets(tmp_path))
    r = client.get("/assets/app-abc.js")
    assert r.status_code == 200 and r.text == "JS-BYTES"
