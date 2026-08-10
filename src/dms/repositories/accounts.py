import hashlib
import hmac
import os
import re
from ..db import Database, dump_json, utc_now_iso
from ..domain import DomainValidationError, ROLE_ADMIN, ROLE_USER

_USERNAME_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}$")
_N, _R, _P = 16384, 8, 1


def _hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${digest.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt_hex, hash_hex = stored.split("$")
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex),
                                n=int(n), r=int(r), p=int(p))
        return hmac.compare_digest(digest.hex(), hash_hex)
    except (ValueError, TypeError):
        return False


class AccountsRepository:
    def __init__(self, db: Database):
        self._db = db

    def create(self, username, password, role, email=None, *, actor="self"):
        if not _USERNAME_RE.fullmatch(username):
            raise DomainValidationError("invalid_username", repr(username))
        with self._db.transaction():
            if self._db.query_one("SELECT 1 AS x FROM accounts WHERE username = :u",
                                  {"u": username}):
                raise DomainValidationError("account_exists", username)
            now = utc_now_iso()
            self._db.execute(
                """INSERT INTO accounts (username, password_hash, role, email, created_at)
                   VALUES (:u, :h, :r, :e, :now)""",
                {"u": username, "h": _hash_password(password), "r": role,
                 "e": email, "now": now})
            self._db.execute(
                """INSERT INTO audit_log (mutation_class, operation, target_key, actor,
                       before_state, after_state, at)
                   VALUES ('account', 'create', :u, :actor, NULL, :a, :at)""",
                {"u": username, "actor": actor,
                 "a": dump_json({"username": username, "role": role, "email": email}),
                 "at": now})

    def verify(self, username, password) -> str | None:
        row = self._db.query_one(
            "SELECT password_hash, role, disabled FROM accounts WHERE username = :u",
            {"u": username})
        if not row or row["disabled"]:
            return None
        return row["role"] if _verify_password(password, row["password_hash"]) else None

    def set_password(self, username, password):
        self._db.execute("UPDATE accounts SET password_hash = :h WHERE username = :u",
                         {"h": _hash_password(password), "u": username})

    def get(self, username):
        row = self._db.query_one(
            """SELECT username, role, email, disabled, created_at
               FROM accounts WHERE username = :u""", {"u": username})
        return row

    def list(self):
        return self._db.query(
            "SELECT username, role, email, disabled, created_at FROM accounts "
            "ORDER BY username")

    def _audit_account(self, operation, username, before, after, actor, now):
        self._db.execute(
            """INSERT INTO audit_log (mutation_class, operation, target_key, actor,
                   before_state, after_state, at)
               VALUES ('account', :op, :u, :actor, :b, :a, :at)""",
            {"op": operation, "u": username, "actor": actor,
             "b": dump_json(before), "a": dump_json(after), "at": now})

    def set_role(self, username, role, *, actor):
        if role not in (ROLE_USER, ROLE_ADMIN):
            raise DomainValidationError("invalid_role", repr(role))
        with self._db.transaction():
            before = self.get(username)
            if before is None:
                raise KeyError(username)
            self._db.execute("UPDATE accounts SET role = :r WHERE username = :u",
                             {"r": role, "u": username})
            self._audit_account("role", username, before, self.get(username),
                                actor, utc_now_iso())

    def set_disabled(self, username, disabled, *, actor):
        with self._db.transaction():
            before = self.get(username)
            if before is None:
                raise KeyError(username)
            self._db.execute("UPDATE accounts SET disabled = :d WHERE username = :u",
                             {"d": 1 if disabled else 0, "u": username})
            self._audit_account("disabled", username, before, self.get(username),
                                actor, utc_now_iso())

    def active_admin_count(self) -> int:
        """활성 관리자(role='admin' AND disabled=0) 수. 삭제·강등·비활성화 세 경로가
        '마지막 활성 관리자'를 잠그지 못하게 하는 데 쓴다(설계 §2.3 안전장치 2).
        공유 토큰이 항상 admin 이라 완전 잠금은 아니지만 사람 admin 0 명은 사고다."""
        row = self._db.query_one(
            "SELECT COUNT(*) AS c FROM accounts WHERE role = :r AND disabled = 0",
            {"r": ROLE_ADMIN})
        return row["c"]

    def delete(self, username, *, actor):
        """하드 삭제(설계 §2.3): accounts + user_scan_paths(계정 소유 리소스) + 감사를
        한 트랜잭션으로 묶는다 -- 부분 삭제나 감사 누락을 막는다(set_role 이 이미 쓰는
        transaction 관례). FK 가 저장소 전체에 0 건이라(설계 §1-7) requests/audit_log 의
        문자열 actor 는 그대로 남는다 -- 버그가 아니라 이력 보존이다. before_state
        스냅샷은 get()이 password_hash 를 SELECT 에서 빼므로 자연히 해시가 빠진다."""
        with self._db.transaction():
            before = self.get(username)
            if before is None:
                raise KeyError(username)
            self._db.execute("DELETE FROM accounts WHERE username = :u", {"u": username})
            # user_scan_paths 는 username 을 관례로만 참조한다(제약 없음, §1-7). 소유자가
            # 사라지면 아무도 볼 수 없는 데드 로우가 되므로 여기서 함께 지운다.
            self._db.execute("DELETE FROM user_scan_paths WHERE username = :u",
                             {"u": username})
            self._audit_account("delete", username, before, None, actor, utc_now_iso())
