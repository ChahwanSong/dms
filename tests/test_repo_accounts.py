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
    # 숫자만은 거부(2026-10-26 -- 잡 셸의 getent·id 가 uid 로 읽는다), 숫자로 시작만 하는 것은 된다
    for numeric in ("1234", "0", "0000000000"):
        with pytest.raises(DomainValidationError) as e:
            repo.create(numeric, "pw", ROLE_USER)
        assert e.value.reason_code == "username_numeric_only"
    repo.create("1user", "pw", ROLE_USER)
    repo.create("12.34", "pw", ROLE_USER)
    # 잡 이미지 시스템 계정 이름은 거부(2026-10-26 -- 실행 신원 0:0 별칭이 daemon=1:1 같은 기존 계정과 충돌), 비슷한 이름은 된다
    for reserved in ("daemon", "nobody", "www-data", "sshd"):
        with pytest.raises(DomainValidationError) as e:
            repo.create(reserved, "pw", ROLE_USER)
        assert e.value.reason_code == "username_reserved"
    repo.create("root", "pw", ROLE_USER)          # root 는 이미지에서도 0:0 -- 별칭과 충돌하지 않는다(특권 목록 기본 이름)
    repo.create("daemons", "pw", ROLE_USER)


def test_set_password(db):
    repo = AccountsRepository(db)
    repo.create("carol", "old", ROLE_USER)
    repo.set_password("carol", "new")
    assert repo.verify("carol", "old") is None
    assert repo.verify("carol", "new") == ROLE_USER


def test_create_is_audited_without_password(db):
    repo = AccountsRepository(db)
    repo.create("alice", "pw", ROLE_USER, email="a@x", actor="admin-token")
    row = db.query("SELECT * FROM audit_log WHERE mutation_class = 'account'")[0]
    assert row["operation"] == "create" and row["target_key"] == "alice"
    assert "password" not in (row["after_state"] or "")
    assert "scrypt" not in (row["after_state"] or "")


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
