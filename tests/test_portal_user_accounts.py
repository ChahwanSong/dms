"""End-user self-service accounts (아이디/비밀번호 + 이메일 6자리 인증번호).

Replaces the old dummy-AD login. The end-user id is the company-mail local part and
the only authorization for creating / re-keying an account is a code mailed to
``<id>@PORTAL_EMAIL_DOMAIN`` — so these tests pin the properties that make that
safe, not just the happy path:

* reserved ids (notably ``root``) can never be registered — DMS treats ``root`` as a
  privileged requester, so a self-registered ``root`` would run data jobs as uid 0;
* the code is never stored in plaintext and never returned over HTTP;
* ``request-code`` answers identically whether or not the id exists (no enumeration);
* a wrong code is counted, and the same code cannot be redeemed twice.

Exercised through the real auth router with an in-memory FakeDb, mirroring
tests/test_portal_accounts.py.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from starlette.middleware.sessions import SessionMiddleware

from portal.backend.auth import auth_router
from portal.backend.config import Settings
from portal.backend.db import hash_password, verify_password
from portal.backend.email_codes import RESERVED_USERNAMES, code_digest

AUTH = "/api/auth"
DOMAIN = "gmail.com"


class FakeDb:
    """In-memory stand-in for the two new tables."""

    configured = True

    def __init__(self) -> None:
        self.users: dict[str, dict[str, Any]] = {}
        self.codes: dict[tuple[str, str], dict[str, Any]] = {}
        self.sent: list[tuple[str, str]] = []  # (to_addr, kind)

    # --- user_accounts ---
    async def user_auth_record(self, username):
        r = self.users.get(username)
        if not r:
            return None
        return {
            "password_hash": r["password_hash"], "is_active": r["is_active"],
            "email": r["email"], "posix_username": r.get("posix_username"),
        }

    async def user_exists(self, username):
        return username in self.users

    async def touch_user_login(self, username):
        self.users[username]["last_login_at"] = datetime.now(UTC)

    # --- email_verifications ---
    async def issue_verification(
        self, *, username, purpose, email, code_hash, ttl_seconds, cooldown_seconds,
        window_seconds, max_sends, failure_window_seconds, max_failures,
        will_mail=True, requested_ip=None,
    ):
        now = datetime.now(UTC)
        prev = self.codes.get((username, purpose))
        if prev is not None:
            if (now - prev["last_sent_at"]).total_seconds() < cooldown_seconds:
                return None
            if prev["sends"] >= max_sends:
                return None
            if prev["failures"] >= max_failures:
                return None
        self.codes[(username, purpose)] = {
            "email": email, "code_hash": code_hash,
            "expires_at": now + timedelta(seconds=ttl_seconds),
            "attempts": 0, "consumed_at": None,
            "sends": (prev["sends"] + 1) if prev else 1,
            "mailed": (prev["mailed"] if prev else 0) + (1 if will_mail else 0),
            "failures": prev["failures"] if prev else 0,
            "last_sent_at": now,
        }
        return {"expires_at": now + timedelta(seconds=ttl_seconds), "sends": 1}

    async def discard_verification(self, *, username, purpose):
        row = self.codes.get((username, purpose))
        if not row or row["consumed_at"] is not None:
            return 0
        row["expires_at"] = datetime.now(UTC)  # expire the code, KEEP the counters
        return 1

    async def verification_matches(self, *, username, purpose, code_hash, max_attempts):
        row = self.codes.get((username, purpose))
        return bool(
            row
            and row["consumed_at"] is None
            and row["code_hash"] == code_hash
            and row["expires_at"] > datetime.now(UTC)
            and row["attempts"] < max_attempts
        )

    async def global_sends_last_hour(self, window_seconds):
        return sum(r["mailed"] for r in self.codes.values())

    def _match(self, username, purpose, code_hash, max_attempts):
        row = self.codes.get((username, purpose))
        if not row or row["consumed_at"] is not None:
            return None
        if row["code_hash"] != code_hash:
            return None
        if row["expires_at"] <= datetime.now(UTC) or row["attempts"] >= max_attempts:
            return None
        row["consumed_at"] = datetime.now(UTC)
        return row

    async def consume_verification_register(
        self, *, username, code_hash, password_hash, max_attempts
    ):
        row = self._match(username, "register", code_hash, max_attempts)
        if row is None:
            return {"matched": 0, "created": 0}
        if username in self.users:
            return {"matched": 1, "created": 0}
        self.users[username] = {
            "password_hash": password_hash, "email": row["email"],
            "is_active": True, "posix_username": None,
        }
        return {"matched": 1, "created": 1}

    async def consume_verification_reset(
        self, *, username, code_hash, password_hash, max_attempts
    ):
        row = self._match(username, "reset", code_hash, max_attempts)
        if row is None:
            return {"matched": 0, "updated": 0}
        u = self.users.get(username)
        if not u or not u["is_active"]:
            return {"matched": 1, "updated": 0}
        u["password_hash"] = password_hash
        u["email"] = row["email"]
        return {"matched": 1, "updated": 1}

    async def bump_verification_failure(self, *, username, purpose, failure_window_seconds):
        row = self.codes.get((username, purpose))
        if not row:
            return {"attempts": 0, "failures": 0}
        row["attempts"] += 1
        row["failures"] += 1
        return {"attempts": row["attempts"], "failures": row["failures"]}


def make_client(**overrides) -> tuple[TestClient, FakeDb, Settings]:
    kwargs = dict(
        session_secret="t" * 32,
        allow_insecure_defaults=True,
        email_domain=DOMAIN,
        # dev echo keeps SMTP out of the tests while still exercising the real
        # fail-closed check in the route (email_configured is False here).
        email_dev_echo=True,
    )
    kwargs.update(overrides)
    settings = Settings(**kwargs)
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-session")
    db = FakeDb()
    app.state.db = db
    app.include_router(auth_router(settings))
    return TestClient(app), db, settings


def request_code(c, username, purpose="register"):
    return c.post(f"{AUTH}/user/request-code", json={"username": username, "purpose": purpose})


def live_code(db: FakeDb, settings: Settings, username: str, purpose: str) -> str:
    """Brute-force the 6-digit code back out of the stored HMAC.

    Only possible here because the test knows the pepper; it also proves the row
    holds a digest rather than the code itself.
    """
    want = db.codes[(username, purpose)]["code_hash"]
    for n in range(1_000_000):
        code = f"{n:06d}"
        if code_digest(settings, purpose=purpose, username=username, code=code) == want:
            return code
    raise AssertionError("code not recoverable")


# --- config endpoint --------------------------------------------------------


def test_user_config_exposes_domain_but_never_smtp_secrets():
    c, _, _ = make_client(smtp_host="smtp.example.com", smtp_password="hunter2")
    body = c.get(f"{AUTH}/user/config").json()
    assert body["email_domain"] == DOMAIN
    assert body["code_ttl_seconds"] == 600  # 요구사항: 10분
    assert body["code_length"] == 6
    assert body["available"] is True
    blob = str(body)
    assert "hunter2" not in blob and "smtp.example.com" not in blob


# --- username policy (the privilege-escalation guard) -----------------------


@pytest.mark.parametrize("bad", ["root", "ROOT", "admin", "operator", "dms", "admin_ops"])
def test_reserved_usernames_cannot_request_a_code(bad):
    """`root` is a DMS privileged requester (uid/gid 0, bypasses the uid floor) and
    the portal passes the login id through as the DMS requester_id."""
    c, _, _ = make_client()
    assert request_code(c, bad).status_code == 422
    assert "root" in RESERVED_USERNAMES


@pytest.mark.parametrize(
    "bad", ["a b", "a,b", "x\ny", ".lead", "trail.", "a..b", "$(id)", ""]
)
def test_malformed_usernames_rejected(bad):
    c, _, _ = make_client()
    assert request_code(c, bad).status_code == 422


@pytest.mark.parametrize(
    "bypass", ["root\n@gmail.com", "admin\n@gmail.com", "  root  @gmail.com"]
)
def test_reserved_name_cannot_be_smuggled_past_the_regex(bypass):
    """Regression: Python's `$` matches before a TRAILING newline, so `^...$` accepted
    "root\\n" — which then missed the RESERVED_USERNAMES lookup (the set holds "root")
    while carrying a CR/LF into the mail headers. Fixed by anchoring with \\Z and
    re-stripping around the '@'."""
    c, _, _ = make_client()
    assert request_code(c, bypass).status_code == 422


def test_foreign_domain_is_rejected_not_truncated():
    """Accepting 'victim@evil.com' by silently keeping the local part would let the
    caller pick the delivery domain."""
    c, _, _ = make_client()
    assert request_code(c, "someone@evil.com").status_code == 422
    assert request_code(c, f"someone@{DOMAIN}").status_code == 202


def test_allowlist_blocks_non_listed_ids():
    c, _, _ = make_client(signup_allowlist=("skychahwan",))
    assert request_code(c, "skychahwan").status_code == 202
    assert request_code(c, "someone.else").status_code == 403


def test_allowlist_does_not_apply_to_password_reset():
    """The allowlist gates who may CREATE an account. Applying it to reset would
    answer 403-vs-202 by membership (the enumeration channel the uniform 202 closes)
    and would lock an existing user out of their own password if they were later
    removed from the list."""
    c, db, _ = make_client(signup_allowlist=("skychahwan",))
    db.users["older.user"] = {
        "password_hash": hash_password("oldpw12345"), "email": f"older.user@{DOMAIN}",
        "is_active": True,
    }
    assert request_code(c, "older.user", purpose="reset").status_code == 202
    # …and an unknown id on the reset path still gets the same 202, not a 403.
    assert request_code(c, "not.listed", purpose="reset").status_code == 202


# --- request-code: no enumeration, code never leaves the server -------------


def test_request_code_response_is_identical_for_new_and_existing_ids():
    c, db, settings = make_client()
    fresh = request_code(c, "new.person")
    db.users["taken"] = {
        "password_hash": hash_password("x" * 10), "email": f"taken@{DOMAIN}",
        "is_active": True,
    }
    existing = request_code(c, "taken")
    assert fresh.status_code == existing.status_code == 202
    assert fresh.json() == existing.json()


def test_code_is_never_in_the_response_and_never_stored_plaintext():
    c, db, settings = make_client()
    body = request_code(c, "test1.user").json()
    code = live_code(db, settings, "test1.user", "register")
    assert code not in str(body)
    row = db.codes[("test1.user", "register")]
    assert row["code_hash"] != code and len(row["code_hash"]) == 64


def test_request_code_fails_closed_without_email_delivery():
    """No relay => no proof of mailbox ownership is possible, so signup must stop."""
    c, _, _ = make_client(email_dev_echo=False)
    assert request_code(c, "test1.user").status_code == 503


def test_request_code_rejects_a_password_field():
    """The server must not hold a password chosen by whoever asked for the code."""
    c, _, _ = make_client()
    r = c.post(
        f"{AUTH}/user/request-code",
        json={"username": "test1.user", "purpose": "register", "password": "sneaky123"},
    )
    assert r.status_code == 422


def test_resend_is_rate_limited():
    c, _, _ = make_client()
    assert request_code(c, "test1.user").status_code == 202
    r = request_code(c, "test1.user")
    assert r.status_code == 429 and r.headers.get("Retry-After")


def test_existing_account_is_rate_limited_identically_to_a_new_one():
    """Regression (enumeration oracle): the 'already registered' branch used to skip
    issue_verification, so it answered 202 forever while the new-account branch started
    answering 429 on the second call — a 100%-reliable existence check. It also meant
    that branch could be replayed without limit, mailing the same address repeatedly
    without ever incrementing the global send counter."""
    c, db, _ = make_client()
    db.users["taken"] = {
        "password_hash": hash_password("x" * 10), "email": f"taken@{DOMAIN}",
        "is_active": True,
    }
    assert request_code(c, "taken").status_code == 202
    first_repeat = request_code(c, "taken")
    assert request_code(c, "fresh.person").status_code == 202
    second_repeat = request_code(c, "fresh.person")
    # Identical treatment whether or not the account exists.
    assert first_repeat.status_code == second_repeat.status_code == 429
    # …and the existing-account path is charged against the send quota.
    assert db.codes[("taken", "register")]["sends"] >= 1


def test_reset_for_unknown_id_is_also_rate_limited():
    c, db, _ = make_client()
    assert request_code(c, "ghost.user", purpose="reset").status_code == 202
    assert request_code(c, "ghost.user", purpose="reset").status_code == 429


def test_no_mail_requests_do_not_consume_the_global_send_budget():
    """Regression: the hourly cap is a RELAY-abuse budget. Counting requests that mail
    nothing (a reset for an unknown id) let anyone exhaust it by cycling ids and take
    signup/reset down for every real user — without a single mail being sent."""
    c, db, _ = make_client()
    for i in range(8):
        assert request_code(c, f"ghost{i}.user", purpose="reset").status_code == 202
    assert await_sum(db, "mailed") == 0   # nothing was mailed …
    assert await_sum(db, "sends") == 8    # … but every request was still throttled


def await_sum(db: FakeDb, field: str) -> int:
    return sum(r[field] for r in db.codes.values())


# --- register ---------------------------------------------------------------


def test_register_with_valid_code_creates_account_and_allows_login():
    c, db, settings = make_client()
    request_code(c, "test1.user")
    code = live_code(db, settings, "test1.user", "register")
    r = c.post(
        f"{AUTH}/user/register",
        json={"username": "test1.user", "password": "userpw1234", "code": code},
    )
    assert r.status_code == 201
    assert verify_password("userpw1234", db.users["test1.user"]["password_hash"])
    assert db.users["test1.user"]["email"] == f"test1.user@{DOMAIN}"

    login = c.post(
        f"{AUTH}/user/login", json={"username": "test1.user", "password": "userpw1234"}
    )
    assert login.status_code == 200
    user = login.json()["user"]
    assert user["role"] == "user"  # never operator


def test_wrong_code_is_counted_and_rejected():
    c, db, settings = make_client()
    request_code(c, "test1.user")
    good = live_code(db, settings, "test1.user", "register")
    bad = "000000" if good != "000000" else "111111"
    r = c.post(
        f"{AUTH}/user/register",
        json={"username": "test1.user", "password": "userpw1234", "code": bad},
    )
    assert r.status_code == 400
    assert db.codes[("test1.user", "register")]["attempts"] == 1
    assert "test1.user" not in db.users


def test_code_cannot_be_redeemed_twice():
    c, db, settings = make_client()
    request_code(c, "test1.user")
    code = live_code(db, settings, "test1.user", "register")
    body = {"username": "test1.user", "password": "userpw1234", "code": code}
    assert c.post(f"{AUTH}/user/register", json=body).status_code == 201
    assert c.post(f"{AUTH}/user/register", json=body).status_code == 400


def test_register_code_cannot_be_replayed_as_a_reset():
    """purpose is bound into the HMAC, so a signup code is useless for reset."""
    c, db, settings = make_client()
    request_code(c, "test1.user")
    code = live_code(db, settings, "test1.user", "register")
    c.post(
        f"{AUTH}/user/register",
        json={"username": "test1.user", "password": "userpw1234", "code": code},
    )
    r = c.post(
        f"{AUTH}/user/reset-password",
        json={"username": "test1.user", "new_password": "attacker99", "code": code},
    )
    assert r.status_code == 400


def test_short_password_rejected():
    c, db, settings = make_client()
    request_code(c, "test1.user")
    code = live_code(db, settings, "test1.user", "register")
    r = c.post(
        f"{AUTH}/user/register",
        json={"username": "test1.user", "password": "short", "code": code},
    )
    assert r.status_code == 422


# --- reset ------------------------------------------------------------------


def test_reset_password_with_code_rekeys_the_account():
    c, db, settings = make_client()
    db.users["test1.user"] = {
        "password_hash": hash_password("oldpw12345"), "email": f"test1.user@{DOMAIN}",
        "is_active": True,
    }
    assert request_code(c, "test1.user", purpose="reset").status_code == 202
    code = live_code(db, settings, "test1.user", "reset")
    r = c.post(
        f"{AUTH}/user/reset-password",
        json={"username": "test1.user", "new_password": "brandnew123", "code": code},
    )
    assert r.status_code == 200
    assert verify_password("brandnew123", db.users["test1.user"]["password_hash"])


def test_reset_for_unknown_id_answers_202_without_creating_an_account():
    """A rate-limit row IS written (so the response/limits match the known-id path),
    but no account is created and no mail is sent."""
    c, db, _ = make_client()
    r = request_code(c, "ghost.user", purpose="reset")
    assert r.status_code == 202
    assert "ghost.user" not in db.users


def test_invalid_purpose_rejected():
    c, _, _ = make_client()
    assert request_code(c, "test1.user", purpose="delete_account").status_code == 422


# --- login ------------------------------------------------------------------


def test_login_rejects_disabled_account():
    c, db, _ = make_client()
    db.users["test1.user"] = {
        "password_hash": hash_password("userpw1234"), "email": f"test1.user@{DOMAIN}",
        "is_active": False,
    }
    r = c.post(
        f"{AUTH}/user/login", json={"username": "test1.user", "password": "userpw1234"}
    )
    assert r.status_code == 401


def test_user_login_never_matches_an_operator_account():
    """Operator credentials live in a different table; the user route must not see
    them (otherwise an operator id could get a user session and vice versa)."""
    c, db, _ = make_client()
    r = c.post(f"{AUTH}/user/login", json={"username": "admin", "password": "admin1234"})
    assert r.status_code == 401


def test_dummy_ad_login_route_is_gone():
    """The unauthenticated login path that accepted any id must not come back."""
    c, _, _ = make_client()
    assert c.post(f"{AUTH}/login/ad", json={"username": "root"}).status_code == 404
