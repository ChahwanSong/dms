# tests/test_repo_agents.py
from dms.db import iso_plus
from dms.repositories.agents import AgentsRepository

REPORT = {"mounts": [], "tools": [], "identities": [], "os": {}}


def test_iso_plus_handles_negative_and_boundaries():
    assert iso_plus("2026-08-02T00:00:10Z", -20) == "2026-08-01T23:59:50Z"
    assert iso_plus("2026-08-02T23:59:50Z", 20) == "2026-08-03T00:00:10Z"


def test_ingest_writes_history_and_current(db):
    repo = AgentsRepository(db)
    repo.ingest("node-a", REPORT, reported_at="2026-08-02T10:00:00Z")
    repo.ingest("node-a", REPORT, reported_at="2026-08-02T10:01:00Z")
    history = db.query("SELECT node_name, reported_at FROM agent_reports ORDER BY id")
    assert [h["reported_at"] for h in history] == [
        "2026-08-02T10:00:00Z", "2026-08-02T10:01:00Z"]
    current = db.query("SELECT node_name, reported_at FROM agent_nodes")
    assert current == [{"node_name": "node-a", "reported_at": "2026-08-02T10:01:00Z"}]


def test_list_nodes_computes_freshness_at_read_time(db):
    repo = AgentsRepository(db)
    repo.ingest("node-a", REPORT, reported_at="2026-08-02T10:00:00Z")
    repo.ingest("node-b", REPORT, reported_at="2026-08-02T09:00:00Z")
    nodes = repo.list_nodes(stale_seconds=300, now_iso="2026-08-02T10:04:00Z")
    assert [(n["node_name"], n["fresh"]) for n in nodes] == [
        ("node-a", True), ("node-b", False)]
    assert nodes[0]["report"] == REPORT
    fresh = repo.fresh_reports(stale_seconds=300, now_iso="2026-08-02T10:04:00Z")
    assert [n["node_name"] for n in fresh] == ["node-a"]


def test_node_reports_newest_first_with_limit(db):
    repo = AgentsRepository(db)
    for minute in (0, 1, 2):
        repo.ingest("node-a", {"seq": minute},
                    reported_at=f"2026-08-02T10:0{minute}:00Z")
    rows = repo.node_reports("node-a", limit=2)
    assert [r["report"]["seq"] for r in rows] == [2, 1]


def test_prune_reports_keeps_current_and_recent(db):
    repo = AgentsRepository(db)
    repo.ingest("node-a", REPORT, reported_at="2026-08-01T00:00:00Z")
    repo.ingest("node-a", REPORT, reported_at="2026-08-02T10:00:00Z")
    deleted = repo.prune_reports("2026-08-02T00:00:00Z", batch_size=1)
    assert deleted == 1
    assert len(db.query("SELECT id FROM agent_reports")) == 1
    assert db.query_one("SELECT reported_at FROM agent_nodes WHERE node_name = 'node-a'")[
        "reported_at"] == "2026-08-02T10:00:00Z"
