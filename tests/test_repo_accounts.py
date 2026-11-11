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


# --- 2026-11-03 세션 세대·관리자 재설정·인증 잠금 해제 ---

def _epoch(db, username):
    return db.query_one("SELECT session_epoch FROM accounts WHERE username = :u", {"u": username})["session_epoch"]


def test_create_sets_a_random_session_epoch(db):
    import re
    repo = AccountsRepository(db)
    repo.create("alice", "pw", ROLE_USER)
    repo.create("bob", "pw", ROLE_USER)
    assert re.fullmatch(r"[0-9a-f]{32}", _epoch(db, "alice"))
    assert _epoch(db, "alice") != _epoch(db, "bob")
    # get()/list() 는 세대를 싣지 않는다(감사 스냅숏에 토큰이 남지 않게)
    assert "session_epoch" not in repo.get("alice")
    assert all("session_epoch" not in r for r in repo.list())


def test_every_password_change_rotates_the_epoch(db):
    repo = AccountsRepository(db)
    repo.create("carol", "pw0", ROLE_USER)
    seen = {_epoch(db, "carol")}
    repo.set_password("carol", "pw1")
    seen.add(_epoch(db, "carol"))
    repo.reset_password("carol", "pw2", actor="carol")
    seen.add(_epoch(db, "carol"))
    repo.admin_reset_password("carol", "pw3", actor="boss")
    seen.add(_epoch(db, "carol"))
    assert len(seen) == 4                                    # 같은 초에 일어나도 매번 다르다(시각이 아니라 난수)
    assert repo.verify("carol", "pw3") == ROLE_USER
    # 역할·비활성 변경은 회전하지 않는다
    before = _epoch(db, "carol")
    repo.set_role("carol", ROLE_ADMIN, actor="t")
    repo.set_disabled("carol", True, actor="t")
    assert _epoch(db, "carol") == before


def test_admin_reset_password_audits_and_rejects_missing(db):
    from dms.db import load_json
    repo = AccountsRepository(db)
    repo.create("dave", "old", ROLE_USER)
    repo.admin_reset_password("dave", "new", actor="boss")
    row = db.query_one("SELECT actor, before_state, after_state FROM audit_log WHERE operation = 'password_admin_reset'")
    assert row["actor"] == "boss"
    assert load_json(row["before_state"]) == {"username": "dave"}
    assert load_json(row["after_state"]) == {"username": "dave", "sessions_revoked": True}
    with pytest.raises(KeyError):
        repo.admin_reset_password("ghost", "x", actor="boss")


def test_authenticate_returns_role_and_epoch(db):
    repo = AccountsRepository(db)
    repo.create("erin", "pw", ROLE_ADMIN)
    assert repo.authenticate("erin", "pw") == {"role": ROLE_ADMIN, "session_epoch": _epoch(db, "erin")}
    assert repo.authenticate("erin", "nope") is None
    assert repo.authenticate("nobody", "pw") is None
    repo.set_disabled("erin", True, actor="t")
    # 2026-11-11: 비밀번호가 맞은 비활성 계정은 None 이 아니라 {"disabled": True}(로그인이 401 account_disabled 로 알린다).
    # role·session_epoch 는 없다 -- 세션 재료가 되지 않게. 틀린 비밀번호는 비활성이어도 None(비활성인지 알리지 않는다).
    assert repo.authenticate("erin", "pw") == {"disabled": True}
    assert repo.authenticate("erin", "nope") is None
    assert repo.verify("erin", "pw") is None
    assert repo.session_state("erin") == {"role": ROLE_ADMIN, "disabled": 1, "session_epoch": _epoch(db, "erin")}
    assert repo.session_state("nobody") is None


def _fail_rows(db, rows):
    for username, purpose, window_start, failures in rows:
        db.execute("""INSERT INTO verification_failures (username, purpose, window_start, failures)
                      VALUES (:u, :p, :w, :f)""", {"u": username, "p": purpose, "w": window_start, "f": failures})


def test_verification_lock_rows_order_window_and_truncation(db):
    repo = AccountsRepository(db)
    now = "2026-11-03T12:00:00Z"
    _fail_rows(db, [
        ("old.one", "signup", "2026-11-02T11:59:59Z", 10),      # 창 지남(24h 초과) -- 제외
        ("b.user", "password_reset", "2026-11-03T10:00:00Z", 3),
        ("a.user", "signup", "2026-11-03T11:00:00Z", 10),       # 잠김
        ("c.user", "signup", "2026-11-03T09:00:00Z", 7),
        ("a.user", "password_reset", "2026-11-03T08:00:00Z", 12),  # 잠김(상한 넘김)
    ])
    rows, truncated = repo.verification_lock_rows(now_iso=now)
    assert truncated is False
    assert [(r["username"], r["purpose"]) for r in rows] == [
        ("a.user", "password_reset"), ("a.user", "signup"), ("c.user", "signup"), ("b.user", "password_reset")]
    locked = rows[1]
    assert locked["locked"] is True and locked["limit"] == 10
    assert locked["retry_after_seconds"] == 23 * 3600              # 첫 실패 11:00 + 24h - 12:00
    assert (rows[2]["locked"], rows[2]["retry_after_seconds"]) == (False, None)
    rows, truncated = repo.verification_lock_rows(limit=2, now_iso=now)
    assert len(rows) == 2 and truncated is True


def test_clear_verification_failures_returns_rows_and_always_audits(db):
    from dms.db import load_json
    repo = AccountsRepository(db)
    _fail_rows(db, [("f.user", "signup", "2026-11-03T10:00:00Z", 10),
                    ("f.user", "password_reset", "2026-11-03T10:00:00Z", 4),
                    ("g.user", "signup", "2026-11-03T10:00:00Z", 2)])
    cleared = repo.clear_verification_failures("f.user", "signup", actor="boss")
    assert cleared == [{"purpose": "signup", "failures": 10, "window_start": "2026-11-03T10:00:00Z"}]
    assert [r["purpose"] for r in db.query("SELECT purpose FROM verification_failures WHERE username = 'f.user'")] \
        == ["password_reset"]
    cleared = repo.clear_verification_failures("f.user", None, actor="boss")     # 두 용도 모두
    assert [r["purpose"] for r in cleared] == ["password_reset"]
    assert repo.clear_verification_failures("nobody", None, actor="boss") == []  # 0행도 정상
    audits = db.query("SELECT target_key, actor, before_state, after_state FROM audit_log "
                      "WHERE mutation_class = 'security' AND operation = 'verification_unlock' ORDER BY id")
    assert [a["target_key"] for a in audits] == ["f.user", "f.user", "nobody"]   # 0행에도 감사
    assert load_json(audits[2]["before_state"]) == {"rows": []} and audits[2]["after_state"] is None
    assert db.query_one("SELECT failures FROM verification_failures WHERE username = 'g.user'")["failures"] == 2
    with pytest.raises(DomainValidationError):
        repo.clear_verification_failures("f.user", "hack", actor="boss")


def test_new_verification_code_is_six_digits():
    from dms.repositories.accounts import new_verification_code
    codes = {new_verification_code() for _ in range(200)}
    assert all(len(c) == 6 and c.isdigit() for c in codes)
    assert len(codes) > 150                                   # 10^6 공간
