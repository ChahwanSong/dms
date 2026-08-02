from dms.repositories.control import ControlRepository


def test_register_and_list(db):
    repo = ControlRepository(db)
    repo.register_probe_target("alice", now_iso="2026-08-02T10:00:00Z")
    repo.register_probe_target("bob", now_iso="2026-08-02T10:30:00Z")
    assert repo.probe_targets(ttl_seconds=3600, now_iso="2026-08-02T10:40:00Z") == [
        "alice", "bob"]


def test_expired_targets_are_dropped(db):
    repo = ControlRepository(db)
    repo.register_probe_target("old", now_iso="2026-08-02T08:00:00Z")
    repo.register_probe_target("new", now_iso="2026-08-02T10:00:00Z")
    assert repo.probe_targets(ttl_seconds=3600, now_iso="2026-08-02T10:30:00Z") == ["new"]
    assert db.query("SELECT username FROM identity_probe_targets") == [
        {"username": "new"}]


def test_reregister_refreshes_ttl(db):
    repo = ControlRepository(db)
    repo.register_probe_target("alice", now_iso="2026-08-02T08:00:00Z")
    repo.register_probe_target("alice", now_iso="2026-08-02T10:00:00Z")
    assert repo.probe_targets(ttl_seconds=3600, now_iso="2026-08-02T10:30:00Z") == ["alice"]
