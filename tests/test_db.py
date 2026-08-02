import re
from dms.db import Database, utc_now_iso


def test_sqlite_roundtrip(tmp_path):
    db = Database.connect(f"sqlite:///{tmp_path}/t.db")
    assert db.dialect == "sqlite"
    db.execute("CREATE TABLE t (a TEXT, b INTEGER)")
    db.execute("INSERT INTO t (a, b) VALUES (:a, :b)", {"a": "x", "b": 1})
    assert db.query("SELECT a, b FROM t WHERE a = :a", {"a": "x"}) == [{"a": "x", "b": 1}]
    assert db.query_one("SELECT b FROM t WHERE a = :a", {"a": "x"}) == {"b": 1}
    assert db.query_one("SELECT b FROM t WHERE a = :a", {"a": "none"}) is None


def test_transaction_rollback_on_error(tmp_path):
    db = Database.connect(f"sqlite:///{tmp_path}/t.db")
    db.execute("CREATE TABLE t (a TEXT PRIMARY KEY)")
    try:
        with db.transaction():
            db.execute("INSERT INTO t (a) VALUES (:a)", {"a": "x"})
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert db.query("SELECT a FROM t") == []


def test_utc_now_iso_format():
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", utc_now_iso())
