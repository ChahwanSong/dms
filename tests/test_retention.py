from dms.repositories import Repositories
from dms.retention import prune_agent_reports_once, prune_events_once


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


def test_prune_events_once_prunes_old_events_only(db):
    repos = Repositories(db)
    # record_event는 at을 항상 utc_now_iso()로 찍어서 과거 시각을 공개 API로 못
    # 넣는다 -- prune_agent_reports_once 테스트가 ingest(reported_at=...)로 하는
    # 것과 달리 여기선 직접 INSERT한다. cutoff = now(2026-08-02) - 30일 = 2026-07-03.
    db.execute("""INSERT INTO events (component, severity, event_type, at)
                  VALUES ('planner', 'error', 'x', :at)""",
              {"at": "2026-07-01T00:00:00Z"})   # cutoff보다 오래됨 -> 지워짐
    db.execute("""INSERT INTO events (component, severity, event_type, at)
                  VALUES ('planner', 'error', 'x', :at)""",
              {"at": "2026-08-01T00:00:00Z"})   # cutoff보다 최근 -> 남음
    deleted = prune_events_once(repos, retention_days=30, now_iso="2026-08-02T00:00:00Z")
    assert deleted == 1
    remaining = db.query("SELECT at FROM events")
    assert remaining == [{"at": "2026-08-01T00:00:00Z"}]
