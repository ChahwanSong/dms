import pytest
from dms.domain import DomainValidationError
from dms.repositories import Repositories
from dms.repositories.scan_paths import covers


@pytest.mark.parametrize("target,path,expected", [
    ("team", "team", True),            # 정확 일치
    ("team", "team/sub", True),        # 상위가 커버
    ("team", "team2", False),          # 접두사지만 다른 디렉터리
    ("team/sub", "team", False),       # 하위 스캔은 상위를 커버하지 못함
    ("./team", "team/sub", True),      # 정규화
    ("team/", "team/sub", True),
    ("a/b", "a/b/c/d", True),
    ("a/b", "a/bc", False),
])
def test_covers(target, path, expected):
    assert covers(target, path) is expected


def test_add_and_list_is_per_user(db):
    repos = Repositories(db)
    repos.scan_paths.add("alice", "s1", "team")
    repos.scan_paths.add("bob", "s1", "team")
    assert [r["path"] for r in repos.scan_paths.list_for("alice")] == ["team"]
    assert len(repos.scan_paths.list_for("bob")) == 1


def test_duplicate_raises(db):
    repos = Repositories(db)
    repos.scan_paths.add("alice", "s1", "team")
    with pytest.raises(DomainValidationError) as e:
        repos.scan_paths.add("alice", "s1", "team")
    assert e.value.reason_code == "scan_path_exists"


def test_get_owned_hides_other_users(db):
    repos = Repositories(db)
    rid = repos.scan_paths.add("alice", "s1", "team")
    assert repos.scan_paths.get_owned(rid, "alice") is not None
    assert repos.scan_paths.get_owned(rid, "bob") is None


def test_delete_owned_only(db):
    repos = Repositories(db)
    rid = repos.scan_paths.add("alice", "s1", "team")
    assert repos.scan_paths.delete_owned(rid, "bob") is False
    assert repos.scan_paths.get_owned(rid, "alice") is not None
    assert repos.scan_paths.delete_owned(rid, "alice") is True
    assert repos.scan_paths.get_owned(rid, "alice") is None
