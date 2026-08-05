from dms.migrations import migrate

EXPECTED = {
    # scan/rm 실행 타임아웃 = 24h (86400). 데드라인이 실제로 발동하게 된 뒤로는 1h 가
    # 대규모 dscan/drm 을 중간에 죽이는 값이라 상향했다 — 운영자가 포탈에서 조정한다.
    "scan":  {"max_nodes": 4, "preview_timeout_seconds": None, "execution_timeout_seconds": 86400},
    "dsync": {"max_nodes": 8, "preview_timeout_seconds": 3600, "execution_timeout_seconds": 259200},
    "nsync": {"max_nodes": 8, "preview_timeout_seconds": 3600, "execution_timeout_seconds": 259200},
    "rm":    {"max_nodes": 4, "preview_timeout_seconds": 1800, "execution_timeout_seconds": 86400},
}


def test_seeds_four_default_policies(db):
    rows = {r["tool"]: r for r in db.query("SELECT * FROM policies")}
    assert set(rows) == set(EXPECTED)
    for tool, want in EXPECTED.items():
        row = rows[tool]
        for key, value in want.items():
            assert row[key] == value, f"{tool}.{key}"
        assert row["procs_per_node"] == 8
        assert row["queue"] == "dms-data"
        assert row["default_priority"] == "mid"
        assert row["max_priority"] == "high"
        assert row["enabled"] == 1
        assert row["updated_by"] == "migration-seed"


def test_seed_is_idempotent_and_never_overwrites(db):
    db.execute("UPDATE policies SET max_nodes = 99, updated_by = 'ops' WHERE tool = 'scan'")
    migrate(db)
    row = db.query_one("SELECT * FROM policies WHERE tool = 'scan'")
    assert row["max_nodes"] == 99
    assert row["updated_by"] == "ops"
    assert len(db.query("SELECT * FROM policies")) == 4
