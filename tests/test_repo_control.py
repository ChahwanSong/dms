import pytest
from dms.domain import DomainValidationError
from dms.repositories.control import ControlRepository


def test_policy_upsert_and_get(db):
    repo = ControlRepository(db)
    assert repo.get_policy("dsync") is None
    repo.upsert_policy("dsync", max_nodes=4, procs_per_node=8, queue="dms-data",
                       default_priority="mid", max_priority="high",
                       preview_timeout_seconds=3600, execution_timeout_seconds=259200,
                       enabled=True, actor="admin")
    assert repo.get_policy("dsync")["max_nodes"] == 4
    with pytest.raises(DomainValidationError):
        repo.upsert_policy("dcp", max_nodes=1, procs_per_node=1, queue="q",
                           default_priority="mid", max_priority="high",
                           preview_timeout_seconds=None, execution_timeout_seconds=60,
                           enabled=True, actor="admin")


def test_denylist_matching(db):
    repo = ControlRepository(db)
    repo.deny("requester", "Mallory", reason="incident", actor="admin")
    repo.deny("group", "blocked-team", reason=None, actor="admin")
    assert repo.is_denied(requester="mallory", owner="x", groups=[]) == "Mallory"
    assert repo.is_denied(requester="a", owner="b", groups=["Blocked-Team"]) == "blocked-team"
    assert repo.is_denied(requester="a", owner="b", groups=["ok"]) is None
    repo.allow("requester", "Mallory", actor="admin")
    assert repo.is_denied(requester="mallory", owner="x", groups=[]) is None


def test_control_state_roundtrip(db):
    repo = ControlRepository(db)
    assert repo.control_state()["maintenance"] == 0
    repo.set_control_state(maintenance=True, drain=False, reason="upgrade", actor="admin")
    st = repo.control_state()
    assert st["maintenance"] == 1 and st["reason"] == "upgrade"


def test_lease_semantics(db):
    repo = ControlRepository(db)
    assert repo.try_acquire_lease("planner", "h1", 30, now_iso="2026-08-02T10:00:00Z")
    assert not repo.try_acquire_lease("planner", "h2", 30, now_iso="2026-08-02T10:00:10Z")
    assert repo.try_acquire_lease("planner", "h1", 30, now_iso="2026-08-02T10:00:10Z")
    assert repo.try_acquire_lease("planner", "h2", 30, now_iso="2026-08-02T10:00:41Z")
