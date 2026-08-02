from dms.repositories import Repositories
from dms.retention import prune_agent_reports_once


def test_prunes_old_history_only(db):
    repos = Repositories(db)
    repos.agents.ingest("n1", {}, reported_at="2026-07-01T00:00:00Z")
    repos.agents.ingest("n1", {}, reported_at="2026-08-01T00:00:00Z")
    deleted = prune_agent_reports_once(repos, retention_days=30,
                                       now_iso="2026-08-02T00:00:00Z")
    assert deleted == 1
    remaining = db.query("SELECT reported_at FROM agent_reports")
    assert remaining == [{"reported_at": "2026-08-01T00:00:00Z"}]
    assert db.query_one("SELECT node_name FROM agent_nodes") == {"node_name": "n1"}


def test_nothing_to_prune_returns_zero(db):
    repos = Repositories(db)
    repos.agents.ingest("n1", {}, reported_at="2026-08-01T00:00:00Z")
    assert prune_agent_reports_once(repos, retention_days=30,
                                    now_iso="2026-08-02T00:00:00Z") == 0
