"""포탈 서브네임(2026-10-02, api/routes_portal.py): 공개 조회, 관리자 저장(감사), 검증, 구형 DB 컬럼 보강."""
import json

import pytest

from dms.db import Database
from dms.migrations import migrate

ADMIN = {"Authorization": "Bearer tok-shared"}


def test_default_is_no_subtitle_and_public(client):
    r = client.get("/api/portal-info")                         # 로그인 없이(로그인 화면이 그린다)
    assert r.status_code == 200 and r.json() == {"subtitle": None}


def test_admin_sets_and_clears_the_subtitle_with_audit(client, db):
    r = client.put("/api/admin/portal-settings", json={"subtitle": "  DAI-CAE  "}, headers=ADMIN)
    assert r.status_code == 200 and r.json() == {"subtitle": "DAI-CAE"}           # 앞뒤 공백 제거
    assert client.get("/api/portal-info").json() == {"subtitle": "DAI-CAE"}
    for empty in ("", "   ", None):
        assert client.put("/api/admin/portal-settings", json={"subtitle": empty},
                          headers=ADMIN).json() == {"subtitle": None}
    rows = db.query("SELECT actor, before_state, after_state FROM audit_log "
                    "WHERE mutation_class = 'portal_settings' ORDER BY id")
    assert json.loads(rows[0]["before_state"]) == {"portal_subtitle": None}
    assert json.loads(rows[0]["after_state"]) == {"portal_subtitle": "DAI-CAE"}
    assert json.loads(rows[1]["after_state"]) == {"portal_subtitle": None}
    assert rows[0]["actor"]


def test_non_admin_cannot_set(client):
    client.post("/api/auth/signup", json={"username": "alice", "password": "p"})
    client.post("/api/auth/login", json={"username": "alice", "password": "p"})
    r = client.put("/api/admin/portal-settings", json={"subtitle": "X"})
    assert r.status_code == 403
    assert client.get("/api/portal-info").json() == {"subtitle": None}


@pytest.mark.parametrize("bad", ["x" * 41, "SSC\nOA", "A\tB", "zero​width", "‮RTL"])
def test_rejects_long_or_invisible_characters(client, bad):
    r = client.put("/api/admin/portal-settings", json={"subtitle": bad}, headers=ADMIN)
    assert r.status_code == 422 and r.json()["detail"] == "invalid_portal_subtitle"


@pytest.mark.parametrize("ok", ["SSC", "DAI-CAE", "DAI-OA", "수원 SSC", "x" * 40, "R&D (1)"])
def test_accepts_site_names(client, ok):
    assert client.put("/api/admin/portal-settings", json={"subtitle": ok}, headers=ADMIN).json() == {"subtitle": ok}


def test_old_database_gets_the_column(tmp_path):
    # 구형 DB(컬럼 없는 control_state)도 migrate 가 _ensure_columns 로 보강한다(CREATE 와 이중 경로).
    db = Database.connect(f"sqlite:///{tmp_path}/old.db")
    migrate(db)
    db.execute("ALTER TABLE control_state DROP COLUMN portal_subtitle")
    assert "portal_subtitle" not in db.query_one("SELECT * FROM control_state WHERE id = 1")
    migrate(db)
    assert db.query_one("SELECT portal_subtitle FROM control_state WHERE id = 1")["portal_subtitle"] is None
