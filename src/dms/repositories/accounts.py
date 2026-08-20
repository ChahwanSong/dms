import hashlib
import hmac
import os
import re
import secrets
from ..db import Database, dump_json, iso_plus, utc_now_iso
from ..domain import DomainValidationError, ROLE_ADMIN, ROLE_USER

_USERNAME_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}$")
_N, _R, _P = 16384, 8, 1

# 계정 셀프서비스 인증번호(2026-08-20): 4자리·5분. 4자리는 사용자 결정(사내
# 이메일 수신 전제) -- 무차별 대입은 시도 상한(5회)으로 막는다: 10^4 공간을
# 5회로는 0.05% 확률이고, 초과 시 코드가 무효라 재발급 전엔 진행 불가.
VERIFICATION_TTL_SECONDS = 300
VERIFICATION_MAX_ATTEMPTS = 5
VERIFICATION_PURPOSES = ("signup", "password_reset")


def valid_username(username: str) -> bool:
    """회사 아이디 형식(cocoa.song 류). 라우트가 이메일 파생 전에 재사용한다."""
    return _USERNAME_RE.fullmatch(username) is not None


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

    def reset_password(self, username, password, *, actor):
        """인증번호 검증을 통과한 비밀번호 변경(셀프서비스). set_password 와 달리
        존재 확인 + 감사를 남긴다 -- 누가 언제 바꿨는지가 계정 변경의 본질이다.
        해시는 감사에 싣지 않는다."""
        with self._db.transaction():
            before = self.get(username)
            if before is None:
                raise DomainValidationError("account_not_found", username)
            self._db.execute(
                "UPDATE accounts SET password_hash = :h WHERE username = :u",
                {"h": _hash_password(password), "u": username})
            self._audit_account("password_reset", username,
                                {"username": username}, {"username": username},
                                actor, utc_now_iso())

    # --- 인증번호(계정 셀프서비스) ---
    def issue_verification_code(self, username, purpose) -> str:
        """4자리 코드 발급(upsert -- (username, purpose)당 최신 1개만 유효).
        재발급은 이전 코드를 무효화한다: 시도 카운터 우회를 막고 '마지막으로
        받은 메일의 코드가 유효'라는 사용자 직관과 일치한다."""
        if purpose not in VERIFICATION_PURPOSES:
            raise DomainValidationError("invalid_verification_purpose", purpose)
        code = f"{secrets.randbelow(10000):04d}"
        now = utc_now_iso()
        with self._db.transaction():
            self._db.execute(
                """DELETE FROM verification_codes
                   WHERE username = :u AND purpose = :p""",
                {"u": username, "p": purpose})
            self._db.execute(
                """INSERT INTO verification_codes
                       (username, purpose, code, expires_at, attempts, created_at)
                   VALUES (:u, :p, :c, :e, 0, :now)""",
                {"u": username, "p": purpose, "c": code,
                 "e": iso_plus(now, VERIFICATION_TTL_SECONDS), "now": now})
        return code

    def consume_verification_code(self, username, purpose, code,
                                  now_iso=None) -> "str | None":
        """검증 성공이면 None(코드는 소비되어 삭제), 실패면 reason_code.
        만료·시도 초과 행은 그 자리에서 지운다 -- 남겨두면 사용자가 왜 안 되는지
        재발급 전까지 영원히 같은 오류만 본다."""
        now = now_iso or utc_now_iso()
        with self._db.transaction():
            row = self._db.query_one(
                """SELECT code, expires_at, attempts FROM verification_codes
                   WHERE username = :u AND purpose = :p""",
                {"u": username, "p": purpose})
            if row is None:
                return "verification_not_found"
            if row["expires_at"] <= now:
                self._db.execute(
                    "DELETE FROM verification_codes WHERE username = :u AND purpose = :p",
                    {"u": username, "p": purpose})
                return "verification_expired"
            if row["attempts"] >= VERIFICATION_MAX_ATTEMPTS:
                self._db.execute(
                    "DELETE FROM verification_codes WHERE username = :u AND purpose = :p",
                    {"u": username, "p": purpose})
                return "verification_too_many_attempts"
            if not hmac.compare_digest(str(row["code"]), str(code)):
                self._db.execute(
                    """UPDATE verification_codes SET attempts = attempts + 1
                       WHERE username = :u AND purpose = :p""",
                    {"u": username, "p": purpose})
                return "verification_invalid"
            self._db.execute(
                "DELETE FROM verification_codes WHERE username = :u AND purpose = :p",
                {"u": username, "p": purpose})
            return None

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
