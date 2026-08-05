import pytest
from dms.domain import DomainValidationError
from dms.repositories.control import ControlRepository


def test_policy_upsert_and_get(db):
    repo = ControlRepository(db)
    db.execute("DELETE FROM policies WHERE tool = 'dsync'")  # 시드된 기본 정책 제거
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
    assert repo.is_denied(requester="mallory", owner="x", groups=[]) == "mallory"
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


def test_denylist_case_insensitive_write_paths(db):
    repo = ControlRepository(db)
    repo.deny("requester", "Mallory", reason="incident", actor="admin")
    repo.deny("requester", "MALLORY", reason="dup", actor="admin")  # 케이스 다른 중복 — 1행 유지
    rows = db.query("SELECT subject FROM identity_denylist WHERE subject_type = 'requester'")
    assert rows == [{"subject": "mallory"}]
    repo.allow("requester", "mAlLoRy", actor="admin")  # 케이스 달라도 삭제됨
    assert repo.is_denied(requester="Mallory", owner="x", groups=[]) is None


def test_audit_entries_returns_latest_first(db):
    repo = ControlRepository(db)
    repo.deny("requester", "u1", reason=None, actor="admin")
    repo.set_control_state(maintenance=True, drain=False, reason="r", actor="admin")
    entries = repo.audit_entries(limit=10)
    assert [e["mutation_class"] for e in entries[:2]] == ["control_state", "denylist"]
    assert repo.audit_entries(limit=1)[0]["mutation_class"] == "control_state"
