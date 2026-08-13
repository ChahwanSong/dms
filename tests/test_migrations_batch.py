from dms.db import Database
from dms.migrations import migrate

def _cols(db, table):
    if db.dialect == "sqlite":
        return {r["name"] for r in db.query(f"PRAGMA table_info({table})")}
    return {r["column_name"] for r in db.query(
        "SELECT column_name FROM information_schema.columns WHERE table_name = :t", {"t": table})}

def test_batch_tables_exist(tmp_path):
    db = Database.connect(f"sqlite:///{tmp_path}/m.db"); migrate(db)
    assert {"batch_id","operation","status","max_concurrency","options","note",
            "item_count","succeeded_count","failed_count",
            "priority","node_count"} <= _cols(db, "batches")
    assert {"batch_id","seq","payload","status","request_id","reason_code"} <= _cols(db, "batch_items")

def test_batch_priority_node_count_backfilled_on_old_db(tmp_path):
    db = Database.connect(f"sqlite:///{tmp_path}/m4.db")
    # 구형: batches를 priority/node_count 없이 만든 뒤 migrate가 ALTER로 보강하는지
    db.execute("""CREATE TABLE batches (batch_id TEXT PRIMARY KEY, operation TEXT,
        requester_id TEXT, actor TEXT, status TEXT, max_concurrency INTEGER,
        options TEXT, note TEXT, item_count INTEGER, succeeded_count INTEGER,
        failed_count INTEGER, created_at TEXT, updated_at TEXT)""")
    migrate(db)
    assert {"priority", "node_count"} <= _cols(db, "batches")

def test_requests_has_batch_id(tmp_path):
    db = Database.connect(f"sqlite:///{tmp_path}/m2.db"); migrate(db)
    assert "batch_id" in _cols(db, "requests")

def test_batch_id_backfilled_on_old_db(tmp_path):
    db = Database.connect(f"sqlite:///{tmp_path}/m3.db")
    # 구형: requests를 batch_id 없이 만든 뒤 migrate가 ALTER로 보강하는지
    db.execute("""CREATE TABLE requests (request_id TEXT PRIMARY KEY, commit_order INTEGER,
        operation TEXT, requester_id TEXT, actor TEXT, resource_key TEXT, priority TEXT,
        payload TEXT, state TEXT, created_at TEXT, updated_at TEXT)""")
    migrate(db)
    assert "batch_id" in _cols(db, "requests")
