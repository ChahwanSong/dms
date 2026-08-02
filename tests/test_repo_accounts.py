import pytest
from dms.domain import DomainValidationError, ROLE_ADMIN, ROLE_USER
from dms.repositories.accounts import AccountsRepository


def test_create_verify_roundtrip(db):
    repo = AccountsRepository(db)
    repo.create("alice", "pw-1", ROLE_USER, email="alice@corp.example")
    assert repo.verify("alice", "pw-1") == ROLE_USER
    assert repo.verify("alice", "wrong") is None
    assert repo.verify("nobody", "pw") is None
    row = repo.get("alice")
    assert row["role"] == ROLE_USER and "password_hash" not in row


def test_duplicate_and_invalid_username(db):
    repo = AccountsRepository(db)
    repo.create("bob", "pw", ROLE_ADMIN)
    with pytest.raises(DomainValidationError) as e:
        repo.create("bob", "pw2", ROLE_USER)
    assert e.value.reason_code == "account_exists"
    with pytest.raises(DomainValidationError) as e:
        repo.create("Bad User!", "pw", ROLE_USER)
    assert e.value.reason_code == "invalid_username"


def test_set_password(db):
    repo = AccountsRepository(db)
    repo.create("carol", "old", ROLE_USER)
    repo.set_password("carol", "new")
    assert repo.verify("carol", "old") is None
    assert repo.verify("carol", "new") == ROLE_USER


def test_concurrent_create_same_username_raises_domain_error(db):
    import threading
    repo = AccountsRepository(db)
    errors = []

    def try_create():
        try:
            repo.create("race", "pw", ROLE_USER)
        except DomainValidationError as e:
            errors.append(e.reason_code)
        except Exception as e:  # raw DB 예외가 새면 실패로 드러나게
            errors.append(type(e).__name__)

    threads = [threading.Thread(target=try_create) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == ["account_exists"]
    assert repo.get("race") is not None
