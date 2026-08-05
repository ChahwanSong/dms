import pytest
from dms.repositories import Repositories


def _mk(repos, name="alice", role="user"):
    repos.accounts.create(name, "pw", role, actor="admin")


def test_set_role_updates_and_audits(db):
    repos = Repositories(db)
    _mk(repos)
    repos.accounts.set_role("alice", "admin", actor="ops")
    assert repos.accounts.get("alice")["role"] == "admin"
    rows = db.query(
        "SELECT * FROM audit_log WHERE mutation_class = 'account' AND operation = 'role'")
    assert len(rows) == 1
    assert rows[0]["target_key"] == "alice"
    assert rows[0]["actor"] == "ops"
    assert rows[0]["before_state"] is not None and rows[0]["after_state"] is not None


def test_set_disabled_updates_and_audits(db):
    repos = Repositories(db)
    _mk(repos)
    repos.accounts.set_disabled("alice", True, actor="ops")
    assert repos.accounts.get("alice")["disabled"] == 1
    # 비활성 계정은 로그인 검증을 통과하지 못한다
    assert repos.accounts.verify("alice", "pw") is None
    repos.accounts.set_disabled("alice", False, actor="ops")
    assert repos.accounts.verify("alice", "pw") == "user"
    rows = db.query(
        "SELECT * FROM audit_log WHERE mutation_class = 'account' AND operation = 'disabled'")
    assert len(rows) == 2


def test_missing_account_raises(db):
    repos = Repositories(db)
    with pytest.raises(KeyError):
        repos.accounts.set_role("nope", "admin", actor="ops")
    with pytest.raises(KeyError):
        repos.accounts.set_disabled("nope", True, actor="ops")


def test_invalid_role_rejected(db):
    from dms.domain import DomainValidationError
    repos = Repositories(db)
    _mk(repos)
    with pytest.raises(DomainValidationError) as e:
        repos.accounts.set_role("alice", "superuser", actor="ops")
    assert e.value.reason_code == "invalid_role"


def test_list_never_exposes_password_hash(db):
    repos = Repositories(db)
    _mk(repos)
    for row in repos.accounts.list():
        assert "password_hash" not in row
