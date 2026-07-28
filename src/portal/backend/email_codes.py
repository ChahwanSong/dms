"""End-user verification codes + username (회사 메일 local-part) validation.

The end-user account flow proves ownership of a company mailbox: the portal mails a
6-digit code to ``<username>@PORTAL_EMAIL_DOMAIN`` and only creates / re-keys the
account once that code comes back. This module owns the two primitives that make
that safe — how a code is generated and stored, and what an acceptable username is.

Storage format is HMAC, **not** PBKDF2 (which ``db.hash_password`` uses for
passwords). The two have opposite requirements:

* A password has huge keyspace, so slowing each guess down is what buys security —
  PBKDF2 240k is right.
* A 6-digit code has only 10^6 candidates, so no iteration count makes an offline
  DB dump safe; what makes it safe is a key that is **not in the database** (the
  pepper). Meanwhile PBKDF2 on an *unauthenticated* endpoint would hand an attacker
  a ~100ms-of-CPU-per-request amplifier.

HMAC also buys a structural win: it is deterministic, so the comparison can live in
the SQL ``WHERE`` clause. That removes the read-then-verify-then-consume window in
which the same code could be redeemed twice.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from typing import TYPE_CHECKING

from fastapi import HTTPException

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .config import Settings

PURPOSE_REGISTER = "register"
PURPOSE_RESET = "reset"
# Server-side whitelist: `purpose` is bound into the HMAC message and used as part of
# the verification row's primary key, so it must never be free-form client input.
ALLOWED_PURPOSES = frozenset({PURPOSE_REGISTER, PURPOSE_RESET})

# The username lands verbatim in three places: an SMTP header/envelope, the
# ``x-dms-actor`` HTTP header, and the DMS ``requester_id`` (a POSIX user name).
# This whitelist is the intersection of what all three accept, so CR/LF, comma,
# semicolon, colon, angle brackets, quotes, backslash, whitespace, NUL and '@' are
# all excluded by construction.
#
# Excluding '@' is the load-bearing one: allowing it would let a caller steer the
# verification mail to an arbitrary external domain, which collapses the entire
# "proves they own the company mailbox" premise of this design.
# \Z, NOT $ — Python's `$` also matches immediately BEFORE a trailing newline, so
# `^...$` accepts "root\n". That string then fails the `in RESERVED_USERNAMES` check
# (the set holds "root") while still carrying a CR/LF into the mail headers: a
# reserved-name bypass and a header-injection primitive in one. \Z matches only at the
# true end of the string.
USER_USERNAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,30}[a-z0-9])?\Z")

# Names that must never be self-registrable.
#
# `root` is the critical one: DMS ships ``dm_allow_root_requester=True`` with
# ``dm_privileged_requesters={"root"}`` and an EMPTY ``dm_privileged_operators``
# (empty == every mTLS actor is authorized), and routers/user_sync.py passes the
# logged-in username straight through as the DMS ``requester_id`` for FS↔FS syncs.
# So a self-registered `root` would run data jobs as uid/gid 0 and bypass the
# ``dm_min_uid``/``dm_min_gid`` floor entirely.
RESERVED_USERNAMES = frozenset(
    {
        "root", "admin", "administrator", "operator", "daemon", "bin", "sys",
        "sysadmin", "nobody", "postgres", "postmaster", "mailer-daemon", "mail",
        "abuse", "security", "noreply", "no-reply", "webmaster", "hostmaster",
        "support", "dms", "portal", "system", "legacy",
    }
)


def generate_code(length: int = 6) -> str:
    """A uniformly random decimal code, leading zeros preserved.

    ``secrets`` (CSPRNG), never ``random`` — the latter's Mersenne Twister state is
    recoverable from observed outputs, which for a shared code space means one
    attacker's own codes predict everyone else's.
    """
    length = max(4, int(length))
    return f"{secrets.randbelow(10 ** length):0{length}d}"


def _pepper(settings: "Settings") -> bytes:
    """The HMAC key. Never stored in the database.

    Falls back to ``session_secret`` (already mandatory and already high-entropy —
    create_app refuses to boot on the dev default) via a domain-separated derivation
    so the two uses can never produce colliding values.
    """
    raw = settings.email_code_pepper or settings.session_secret
    return hashlib.sha256(b"portal.email-code.v1|" + raw.encode()).digest()


def code_digest(settings: "Settings", *, purpose: str, username: str, code: str) -> str:
    """Storage/comparison form of a verification code.

    ``purpose`` and ``username`` are bound into the message, so a code minted for
    signup cannot be replayed against password reset (or against another account)
    even if it is observed.
    """
    msg = f"{purpose}|{username}|{code}".encode()
    return hmac.new(_pepper(settings), msg, hashlib.sha256).hexdigest()


def burn_dummy(settings: "Settings", *, purpose: str, username: str) -> None:
    """Do the same HMAC work on the paths that have nothing to verify.

    Without this, "account exists" and "account does not exist" take measurably
    different amounts of time, which is a user-enumeration oracle.
    """
    code_digest(settings, purpose=purpose, username=username, code="000000")


def normalize_username(
    raw: str, settings: "Settings", *, enforce_allowlist: bool = True
) -> str:
    """Canonicalize and authorize a user-supplied id, or raise HTTPException.

    Lowercased because, although local-parts are case-sensitive per RFC, real
    mailers are not: 'Test1.User' and 'test1.user' reach the same mailbox, so
    treating them as two accounts would be an impersonation primitive.

    ``enforce_allowlist`` is False on the password-reset paths. The allowlist gates
    who may *create* an account, so applying it to reset would (a) answer 403 vs 202
    depending on membership, which is exactly the enumeration channel the uniform
    202 exists to close, and (b) permanently lock an existing user out of their own
    password if they were later removed from the list.
    """
    value = (raw or "").strip().lower()
    if "@" in value:
        # Convenience: accept a pasted full address, but ONLY for the configured
        # domain. Anything else is rejected rather than silently truncated — that
        # keeps "the code goes to a company mailbox" true.
        local, _, domain = value.partition("@")
        # Re-strip: the leading .strip() only trimmed the ends of the WHOLE input, so
        # whitespace adjacent to the '@' survives into the local part.
        local, domain = local.strip(), domain.strip()
        if not settings.email_domain or domain != settings.email_domain:
            raise HTTPException(
                status_code=422,
                detail=(
                    "invalid_email_domain "
                    f"(회사 메일 도메인 @{settings.email_domain or '<미설정>'} 만 사용할 수 있습니다)"
                ),
            )
        value = local
    if not USER_USERNAME_RE.match(value) or ".." in value:
        raise HTTPException(
            status_code=422,
            detail=(
                "invalid_local_part "
                "(아이디는 회사 메일 주소의 @ 앞부분입니다. 소문자/숫자로 시작·끝나며 "
                "'.', '_', '-' 를 포함할 수 있습니다)"
            ),
        )
    # Keep the two account namespaces disjoint so an id can never be ambiguous
    # between the operator store and the user store.
    if value.startswith("admin_"):
        raise HTTPException(
            status_code=422,
            detail="username_reserved ('admin_' 로 시작하는 아이디는 운영자 전용입니다)",
        )
    if (
        value in RESERVED_USERNAMES
        or value == (settings.backup_requester or "").lower()
        or value == (settings.dms_actor or "").lower()
    ):
        raise HTTPException(
            status_code=422,
            detail="username_reserved (시스템 예약 아이디는 사용할 수 없습니다)",
        )
    if enforce_allowlist and settings.signup_allowlist and value not in settings.signup_allowlist:
        raise HTTPException(
            status_code=403,
            detail="signup_not_allowed (가입이 허용된 아이디가 아닙니다. 관리자에게 문의하세요)",
        )
    return value
