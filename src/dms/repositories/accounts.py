import hashlib
import hmac
import os
import re
import secrets
from ..db import Database, dump_json, iso_epoch, iso_plus, utc_now_iso
from ..domain import DomainValidationError, ROLE_ADMIN, ROLE_USER

_USERNAME_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}$")
_N, _R, _P = 16384, 8, 1

# 계정 셀프서비스 인증번호(2026-08-20): 4자리·5분. 4자리는 사용자 결정(사내
# 이메일 수신 전제) -- 무차별 대입은 시도 상한(5회)으로 막는다: 10^4 공간을
# 5회로는 0.05% 확률이고, 초과 시 코드가 무효라 재발급 전엔 진행 불가.
VERIFICATION_TTL_SECONDS = 300
VERIFICATION_MAX_ATTEMPTS = 5
VERIFICATION_PURPOSES = ("signup", "password_reset")
# 누적 실패 상한(2026-10-01 리뷰): 코드당 5회는 재발급마다 초기화돼, Knox 메일로 실운영하면 "5번 틀리고
# 재발급"(수신자 상한 5통/10분)으로 하루 720코드x5회 = 맞힐 확률 ~30%(1주 ~92%) -- 남의 계정 재설정·미가입
# 직원 아이디 선점. (username, purpose)별 실패를 첫 실패부터 24시간 누적해 10회면 그 창이 끝날 때까지 발급·
# 소비를 모두 막는다(맞힐 확률 하루 0.1%). 대가: 남의 아이디로 10번 틀려 그 사람의 재설정을 하루 막을 수
# 있다(로그인은 그대로; 창이 끝나면 저절로 풀린다 -- 급하면 운영자가 verification_failures 의 그 행을 지운다).
VERIFICATION_FAILURE_LIMIT = 10
VERIFICATION_FAILURE_WINDOW_SECONDS = 86400


def new_verification_code() -> str:
    """4자리 숫자(0000~9999). 저장은 issue_verification_code(code=...) 가 한다."""
    return f"{secrets.randbelow(10000):04d}"


def _lock_remaining(row, now: str) -> "int | None":
    """verification_failures 행 -> 잠금이면 남은 초(>= 1), 아니면 None(행 없음·창 지남·상한 미만)."""
    if row is None or row["failures"] < VERIFICATION_FAILURE_LIMIT:
        return None
    remaining = iso_epoch(row["window_start"]) + VERIFICATION_FAILURE_WINDOW_SECONDS - iso_epoch(now)
    return max(1, int(remaining)) if remaining > 0 else None


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
    def issue_verification_code(self, username, purpose, code: "str | None" = None) -> str:
        """4자리 코드 발급(upsert -- (username, purpose)당 최신 1개만 유효).
        재발급은 이전 코드를 무효화한다: 시도 카운터 우회를 막고 '마지막으로
        받은 메일의 코드가 유효'라는 사용자 직관과 일치한다.
        code 를 주면 그 값을 저장한다(2026-10-01 Knox 메일: 메일을 **보낸 뒤에** 저장해, 발송이
        실패하면 메일함에 있던 이전 코드가 그대로 유효하게 -- new_verification_code() 로 먼저 만든다)."""
        if purpose not in VERIFICATION_PURPOSES:
            raise DomainValidationError("invalid_verification_purpose", purpose)
        code = code if code is not None else new_verification_code()
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

    def verification_lock_seconds(self, username, purpose, now_iso=None) -> "int | None":
        """누적 실패 잠금(VERIFICATION_FAILURE_LIMIT)이면 풀리기까지 남은 초(>= 1), 아니면 None."""
        row = self._db.query_one(
            """SELECT window_start, failures FROM verification_failures
               WHERE username = :u AND purpose = :p""", {"u": username, "p": purpose})
        return _lock_remaining(row, now_iso or utc_now_iso())

    def _record_verification_failure(self, username, purpose, now) -> None:
        """트랜잭션 안에서만 부른다. 창(첫 실패부터 24시간)이 지났으면 새 창을 연다.
        ON CONFLICT DO NOTHING 으로 행을 먼저 보장한다 -- 레플리카 둘이 같은 사용자의 첫 실패를 동시에
        INSERT 해 PK 위반(500)이 나는 일이 없게(SQLite 3.24+·PostgreSQL 공통 문법)."""
        self._db.execute(
            """INSERT INTO verification_failures (username, purpose, window_start, failures)
               VALUES (:u, :p, :now, 0) ON CONFLICT (username, purpose) DO NOTHING""",
            {"u": username, "p": purpose, "now": now})
        row = self._db.query_one(
            """SELECT window_start FROM verification_failures
               WHERE username = :u AND purpose = :p""", {"u": username, "p": purpose})
        if iso_epoch(now) - iso_epoch(row["window_start"]) >= VERIFICATION_FAILURE_WINDOW_SECONDS:
            self._db.execute(
                """UPDATE verification_failures SET window_start = :now, failures = 1
                   WHERE username = :u AND purpose = :p""", {"u": username, "p": purpose, "now": now})
        else:
            self._db.execute(
                """UPDATE verification_failures SET failures = failures + 1
                   WHERE username = :u AND purpose = :p""", {"u": username, "p": purpose})

    def consume_verification_code(self, username, purpose, code,
                                  now_iso=None) -> "str | None":
        """검증 성공이면 None(코드는 소비되어 삭제), 실패면 reason_code.
        만료·시도 초과 행은 그 자리에서 지운다 -- 남겨두면 사용자가 왜 안 되는지
        재발급 전까지 영원히 같은 오류만 본다.
        누적 실패 잠금 중이면 코드를 비교하지도 않고 verification_locked(맞는 코드여도 -- 잠금 중 추측이
        통하면 상한이 의미 없다). 틀린 코드는 코드 시도와 누적 실패를 함께 올리고, 성공은 누적을 지운다."""
        now = now_iso or utc_now_iso()
        with self._db.transaction():
            failures = self._db.query_one(
                """SELECT window_start, failures FROM verification_failures
                   WHERE username = :u AND purpose = :p""", {"u": username, "p": purpose})
            if _lock_remaining(failures, now) is not None:
                return "verification_locked"
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
                self._record_verification_failure(username, purpose, now)
                return "verification_invalid"
            self._db.execute(
                "DELETE FROM verification_codes WHERE username = :u AND purpose = :p",
                {"u": username, "p": purpose})
            self._db.execute(
                "DELETE FROM verification_failures WHERE username = :u AND purpose = :p",
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
