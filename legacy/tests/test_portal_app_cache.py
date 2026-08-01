"""Portal BFF static-serving cache policy.

index.html must NOT be cached (so a fresh deploy is picked up immediately),
while content-hashed assets are cached long-term.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from portal.backend.app import create_app
from portal.backend.config import Settings


def _spa(tmp_path):
    (tmp_path / "index.html").write_text("<html><body>spa-marker</body></html>")
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "app-abc123.js").write_text("console.log(1)")
    return tmp_path


def test_index_html_no_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("PORTAL_STATIC_DIR", str(_spa(tmp_path)))
    with TestClient(create_app(Settings(allow_insecure_defaults=True))) as client:
        r = client.get("/")
        assert r.status_code == 200
        assert "spa-marker" in r.text
        assert r.headers.get("cache-control") == "no-cache"


def test_hashed_asset_is_immutable(tmp_path, monkeypatch):
    monkeypatch.setenv("PORTAL_STATIC_DIR", str(_spa(tmp_path)))
    with TestClient(create_app(Settings(allow_insecure_defaults=True))) as client:
        a = client.get("/assets/app-abc123.js")
        assert a.status_code == 200
        cc = a.headers.get("cache-control", "")
        assert "immutable" in cc and "max-age=31536000" in cc
