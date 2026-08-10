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


def test_delete_removes_account_scan_paths_and_audits(db):
    repos = Repositories(db)
    _mk(repos, name="doomed", role="user")
    repos.scan_paths.add("doomed", "ceph-a", "team")
    repos.scan_paths.add("other", "ceph-a", "keep")   # 타인 행은 남는다
    repos.accounts.delete("doomed", actor="ops")
    assert repos.accounts.get("doomed") is None
    assert repos.scan_paths.list_for("doomed") == []
    # 타인의 scan 경로는 연쇄되지 않는다 -- 삭제 대상 소유 리소스만 정리한다.
    assert len(repos.scan_paths.list_for("other")) == 1
    rows = db.query(
        "SELECT * FROM audit_log WHERE mutation_class='account' AND operation='delete'")
    assert len(rows) == 1
    assert rows[0]["target_key"] == "doomed" and rows[0]["actor"] == "ops"
    # before_state 스냅샷은 존재하고 after_state 는 null(하드 삭제), password_hash 없음.
    assert rows[0]["before_state"] is not None
    assert "password_hash" not in rows[0]["before_state"]
    assert rows[0]["after_state"] in (None, "null")


def test_delete_preserves_history_actor_strings(db):
    # FK 가 저장소 전체에 0건이라 requests/audit_log 의 문자열 actor 는 그대로 남는다
    # -- 버그가 아니라 이력 보존이 결정이다(설계 §2.3).
    repos = Repositories(db)
    _mk(repos, name="doomed", role="user")
    rid = repos.requests.create(operation="scan", requester_id="doomed",
                                actor="doomed", resource_key="k",
                                payload={"storage": "s1"}, priority="mid")
    repos.accounts.delete("doomed", actor="ops")
    assert repos.requests.get(rid)["requester_id"] == "doomed"
    created = db.query(
        "SELECT * FROM audit_log WHERE mutation_class='account' AND operation='create'")
    assert created[0]["target_key"] == "doomed"


def test_delete_missing_account_raises(db):
    repos = Repositories(db)
    with pytest.raises(KeyError):
        repos.accounts.delete("ghost", actor="ops")


def test_active_admin_count_ignores_disabled_and_users(db):
    repos = Repositories(db)
    _mk(repos, name="a1", role="admin")
    _mk(repos, name="a2", role="admin")
    _mk(repos, name="u1", role="user")
    repos.accounts.set_disabled("a2", True, actor="ops")   # 비활성 admin 은 제외
    assert repos.accounts.active_admin_count() == 1
