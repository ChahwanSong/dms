import re
import threading
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


def test_json_helpers():
    from dms.db import dump_json, load_json
    assert load_json(dump_json({"b": 1, "a": [1, 2]})) == {"a": [1, 2], "b": 1}
    assert dump_json({"b": 1, "a": 2}) == '{"a": 2, "b": 1}'  # sort_keys
    assert load_json(None) is None
    assert load_json("") is None


def test_transaction_excludes_other_threads(tmp_path):
    db = Database.connect(f"sqlite:///{tmp_path}/t.db")
    db.execute("CREATE TABLE t (a TEXT)")
    entered = threading.Event()

    def rolling_back_txn():
        try:
            with db.transaction():
                db.execute("INSERT INTO t (a) VALUES (:a)", {"a": "doomed"})
                entered.set()
                raise RuntimeError("abort")
        except RuntimeError:
            pass

    worker = threading.Thread(target=rolling_back_txn)
    worker.start()
    entered.wait(timeout=5)
    db.execute("INSERT INTO t (a) VALUES (:a)", {"a": "survivor"})  # 트랜잭션이 끝날 때까지 블록되어야 함
    worker.join(timeout=5)
    rows = db.query("SELECT a FROM t ORDER BY a")
    assert rows == [{"a": "survivor"}]
