"""Portal BFF settings.

Config is env-driven under a ``PORTAL_*`` prefix (kept distinct from the DMS
backend's ``DMS_*`` so the two services never share config state). There is no
config file. Mirrors the DMS backend's ``Settings.from_env()`` convention.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
import re

# The built-in dev session secret. Booting with this (cookie-signing key known
# from source) lets anyone forge an operator session, so create_app() refuses to
# start on it unless PORTAL_ALLOW_INSECURE_DEFAULTS is set (local dev).
DEV_DEFAULT_SESSION_SECRET = "dev-only-insecure-portal-secret-change-me"


def _env_bool(raw: str | None, default: bool) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "")


def _parse_operator_users(raw: str | None) -> dict[str, str]:
    """Parse ``user:password,user2:password2`` into a {user: password} map.

    The operator credential store — id/password login is operator-only, and an
    operator may have multiple id/password accounts (multiple entries here). This
    is a stand-in store for now (no DB, no DMS); replaced later by real auth.
    """
    users: dict[str, str] = {}
    if not raw:
        return users
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair or ":" not in pair:
            continue
        username, _, password = pair.partition(":")
        username = username.strip()
        if username:
            users[username] = password
    return users


def _email_delivery(raw: str | None, default: str) -> str:
    """Whitelist the delivery provider id (mailer.DELIVERY_PROVIDERS).

    Imported lazily to keep config.py free of module-level app imports, matching how
    PoolConfig is imported inside the pool helpers.
    """
    from .mailer import DELIVERY_NONE, DELIVERY_PROVIDERS

    value = (raw or "").strip().lower()
    if not value:
        return default
    # Unknown value -> 'none' (fail closed), never the caller's default: a typo must
    # stop the feature, not silently pick a weaker path.
    return value if value in DELIVERY_PROVIDERS else DELIVERY_NONE


def _email_domain(raw: str | None, default: str) -> str:
    """'@samsung.com' / ' Samsung.COM ' -> 'samsung.com'."""
    value = (raw or "").strip().lstrip("@").lower()
    return value or default


def _signup_allowlist(raw: str | None) -> tuple[str, ...]:
    """'a.b, c.d' -> ('a.b', 'c.d'). Empty => no restriction."""
    if not raw:
        return ()
    return tuple(
        item for item in (p.strip().lower() for p in raw.split(",")) if item
    )


# 부팅 시 PORTAL_EMAIL_DOMAIN 형식 검증용 (app.create_app이 fail-closed로 사용).
EMAIL_DOMAIN_RE = re.compile(
    r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?([.][a-z0-9]([a-z0-9-]*[a-z0-9])?)+$"
)

# 공용 메일 도메인으로 가입을 열어두면 "그 서비스 계정 보유자 전원"이 가입 자격이 된다.
# 회사 도메인이면 도메인 자체가 소속 증명이므로 allowlist 없이도 안전하다.
PUBLIC_EMAIL_DOMAINS = frozenset(
    {
        "gmail.com", "googlemail.com", "naver.com", "daum.net", "hanmail.net",
        "kakao.com", "outlook.com", "hotmail.com", "live.com", "yahoo.com",
        "icloud.com", "proton.me", "protonmail.com", "nate.com",
    }
)


@dataclass(frozen=True)
class Settings:
    # Signs the session cookie. MUST be overridden in any non-local deployment.
    session_secret: str = DEV_DEFAULT_SESSION_SECRET
    session_cookie: str = "dms_portal_session"
    # Cookie lifetime; 0 disables max-age (session cookie cleared on browser close).
    session_max_age_seconds: int = 8 * 60 * 60
    # Secure-cookie flag. Default True (fail-closed); the plain-HTTP testbed sets
    # PORTAL_SESSION_HTTPS_ONLY=false so the cookie is sent over the NodePort.
    session_https_only: bool = True
    # Allow booting with the dev-default session secret (local dev only).
    allow_insecure_defaults: bool = False
    # Operator credential store (PORTAL_OPERATOR_USERS). Defaults to admin/admin1234.
    # id/password login is operator-only; multiple entries == multiple operators.
    operator_users: dict[str, str] = field(
        default_factory=lambda: {"admin": "admin1234"}
    )
    # Admin token (PORTAL_ADMIN_TOKEN) that gates operator-account MANAGEMENT
    # (create / reset others / disable / delete). Deliberately SEPARATE from
    # dms_token (that authenticates the BFF→DMS API and must never leak to the
    # browser). An operator unlocks "관리자 모드" by entering this once; the BFF
    # verifies it server-side and only stores a session flag (the token itself is
    # never returned to or stored in the browser). None => account-management is
    # unavailable (self password change still works).
    admin_token: str | None = None
    # DMS HTTP API base (e.g. http://10.10.10.10:30080). The portal is an
    # API-client of DMS; None means "DMS not wired" and DMS-backed routes 503.
    dms_api_url: str | None = None
    # Testbed auth profile: shared bearer token + x-dms-actor header. (Production
    # uses the mTLS header profile — out of scope here.) The BFF sends the
    # logged-in operator's username as the actor for DMS audit, falling back to
    # dms_actor when no session actor is available.
    dms_token: str | None = None
    dms_actor: str = "operator"
    dms_timeout_seconds: float = 15.0
    # TLS verification for the DMS client (only relevant once DMS is https).
    dms_verify_tls: bool = True

    # --- portal DB (Postgres) -------------------------------------------
    # The portal's OWN persistence (PORTAL_DB_URL, e.g. the DMS Postgres with a
    # dedicated `db_schema`). Holds operator logins + data-backup batches/requests.
    # None => no DB: login falls back to the env store and the data-backup
    # feature is disabled (its routes 503).
    db_url: str | None = None
    db_schema: str = "portal"

    # --- data-backup orchestrator ---------------------------------------
    # Backup sync jobs run as `backup_requester` (default "root" so the original
    # file/dir ownership is preserved — root runs dsync without a chown override).
    # DMS gates privileged root jobs behind an mTLS-verified operator, so the BFF
    # prefixes the x-dms-actor with `backup_actor_prefix` for DM calls (the
    # testbed treats the `mtls:` prefix as verified; production sets it via a real
    # client certificate).
    backup_requester: str = "root"
    backup_actor_prefix: str = "mtls:"
    # Volcano scheduling priority for backup jobs (High/Mid/Low or int). Default
    # Low so background backups don't preempt interactive/operator work.
    backup_priority: str = "Low"
    # Orchestrator: max DMS jobs in flight per batch per cycle, and poll cadence.
    backup_concurrency: int = 8
    backup_poll_seconds: float = 5.0
    # Safety net (B): a preview_pending request that never yields a DMS job (e.g.
    # the request terminated pre-job, or the planner is stuck) is failed after this
    # many seconds so a single item can't park a batch in 'previewing' forever.
    backup_preview_timeout_seconds: float = 900.0

    # --- dashboard time-series sampler ----------------------------------
    # DMS exposes only point-in-time work counts (no history endpoint), so the BFF
    # samples them every N seconds into its own DB (dashboard_samples) to back the
    # request/job trend chart. Retention bounds the table; the 1h–30d chart window
    # fills in from deploy time forward (standard scrape-based monitoring).
    dashboard_sample_seconds: float = 60.0
    dashboard_retention_days: int = 31

    # --- end-user accounts + email verification -------------------------
    # 사용자(end-user)는 회사 메일 local-part를 아이디로 self-service 가입한다.
    # 아이디 소유 증명 = <아이디>@email_domain 으로 보낸 6자리 인증번호. 도메인은 여기(env)에서만
    # 오고 사용자 입력에서 절대 파생하지 않는다 — 파생하면 인증번호를 임의 외부 도메인으로
    # 배달시킬 수 있어 "회사 메일 소유 증명"이라는 전제가 무너진다. 빈 값이면 기능 비활성.
    # DMS 구축 시 회사 도메인으로 설정한다(테스트 중 gmail.com → 운영 samsung.com).
    email_domain: str = ""
    # 사용자 self-service 가입 킬스위치. false면 가입 요청이 403.
    user_signup_enabled: bool = True
    # 가입 허용 local-part 목록(콤마 구분). 빈 값 = 제한 없음. 도메인이 공용(gmail.com)인
    # 동안에는 반드시 채운다 — 비우면 "임의의 gmail 계정 보유자"가 곧 가입 자격이 되고,
    # 가입 사용자는 DMS 데이터 작업 실행 경로를 갖는다.
    signup_allowlist: tuple[str, ...] = ()
    # 인증메일 배송 수단 (PORTAL_EMAIL_DELIVERY): none | log | company.
    #   none    = 배송 수단 없음 → 인증요청이 503으로 fail-closed (기본값)
    #   log     = 인증번호를 서버 로그로만 출력하는 개발 전용 경로
    #             (PORTAL_ALLOW_INSECURE_DEFAULTS 동반 필수)
    #   company = 사내 메일 발송 시스템 (mailer.deliver_company_mail 구현 필요)
    # 알 수 없는 값은 'none'으로 폴백한다 — 오타 하나로 인증 없이 계정이 만들어지는
    # 쪽이 아니라 기능이 멈추는 쪽으로 틀어져야 한다.
    email_delivery: str = "none"
    # 사내 발송 구현이 동기 라이브러리를 쓸 때 반드시 걸어야 하는 상한(초).
    # asyncio.to_thread는 취소가 불가능하므로 타임아웃이 유일한 안전장치다.
    email_send_timeout_seconds: float = 10.0
    # 인증번호 정책. TTL 10분(요구사항).
    email_code_ttl_seconds: int = 600
    email_code_length: int = 6
    email_code_max_attempts: int = 5
    email_resend_cooldown_seconds: int = 60
    email_send_window_seconds: int = 3600
    email_max_sends_per_window: int = 5
    # 재발급으로 리셋되지 않는 누적 실패 예산. attempts만 있으면 "5회 실패 → 재발급 → 5회"가
    # 무한 반복되어 시도 제한이 사실상 없어진다.
    email_max_failures_per_window: int = 20
    email_failure_window_seconds: int = 3600
    # 전역 발송 상한(시간당). 아이디별 제한만으로는 "아이디 1만 개 × 5통"을 못 막고, 메일
    # 릴레이가 포탈을 스팸 발신원으로 차단하면 기능 전체가 죽는다.
    email_global_max_sends_per_hour: int = 200
    # 인증번호 해시용 pepper(DB에 없는 키). 미설정 시 session_secret에서 도메인 분리 파생.
    email_code_pepper: str | None = None

    @property
    def email_dev_echo(self) -> bool:
        """개발 전용 로그 출력 경로가 선택되어 있는지. 실제 사용 가능 여부는
        allow_insecure_defaults와의 이중 게이트이며, 위반 조합이면 create_app()이
        기동을 거부한다(app.py)."""
        from .mailer import DELIVERY_LOG

        return self.email_delivery == DELIVERY_LOG

    @property
    def email_configured(self) -> bool:
        """인증메일을 실제로 보낼 수 있는 상태인지.

        도메인 없이는 수신자 주소를 만들 수 없고, 배송 수단(log/company) 없이는
        보낼 수 없다. 둘 중 하나라도 없으면 인증요청은 503으로 fail-closed 된다.
        """
        from .mailer import DELIVERY_NONE

        if not self.email_domain or self.email_delivery == DELIVERY_NONE:
            return False
        if self.email_dev_echo:
            return self.allow_insecure_defaults
        return True

    @property
    def dms_configured(self) -> bool:
        return bool(self.dms_api_url)

    @property
    def db_configured(self) -> bool:
        return bool(self.db_url)

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Settings":
        env = env if env is not None else dict(os.environ)
        # Prefer PORTAL_OPERATOR_USERS; fall back to the older PORTAL_LOCAL_USERS.
        operator_users = _parse_operator_users(
            env.get("PORTAL_OPERATOR_USERS") or env.get("PORTAL_LOCAL_USERS")
        )
        defaults = cls()
        return cls(
            session_secret=env.get("PORTAL_SESSION_SECRET", defaults.session_secret),
            session_cookie=env.get("PORTAL_SESSION_COOKIE", defaults.session_cookie),
            session_max_age_seconds=int(
                env.get(
                    "PORTAL_SESSION_MAX_AGE_SECONDS",
                    defaults.session_max_age_seconds,
                )
            ),
            session_https_only=_env_bool(
                env.get("PORTAL_SESSION_HTTPS_ONLY"), defaults.session_https_only
            ),
            allow_insecure_defaults=_env_bool(
                env.get("PORTAL_ALLOW_INSECURE_DEFAULTS"), False
            ),
            operator_users=operator_users or defaults.operator_users,
            admin_token=env.get("PORTAL_ADMIN_TOKEN") or None,
            dms_api_url=env.get("PORTAL_DMS_API_URL") or None,
            dms_token=env.get("PORTAL_DMS_TOKEN") or None,
            dms_actor=env.get("PORTAL_DMS_ACTOR", defaults.dms_actor),
            dms_timeout_seconds=float(
                env.get("PORTAL_DMS_TIMEOUT_SECONDS", defaults.dms_timeout_seconds)
            ),
            dms_verify_tls=env.get("PORTAL_DMS_VERIFY_TLS", "true").lower()
            not in ("0", "false", "no"),
            db_url=env.get("PORTAL_DB_URL") or None,
            db_schema=env.get("PORTAL_DB_SCHEMA", defaults.db_schema),
            backup_requester=env.get(
                "PORTAL_BACKUP_REQUESTER", defaults.backup_requester
            ),
            backup_actor_prefix=env.get(
                "PORTAL_BACKUP_ACTOR_PREFIX", defaults.backup_actor_prefix
            ),
            backup_priority=env.get("PORTAL_BACKUP_PRIORITY", defaults.backup_priority),
            backup_concurrency=int(
                env.get("PORTAL_BACKUP_CONCURRENCY", defaults.backup_concurrency)
            ),
            backup_poll_seconds=float(
                env.get("PORTAL_BACKUP_POLL_SECONDS", defaults.backup_poll_seconds)
            ),
            backup_preview_timeout_seconds=float(
                env.get(
                    "PORTAL_BACKUP_PREVIEW_TIMEOUT_SECONDS",
                    defaults.backup_preview_timeout_seconds,
                )
            ),
            dashboard_sample_seconds=float(
                env.get(
                    "PORTAL_DASHBOARD_SAMPLE_SECONDS",
                    defaults.dashboard_sample_seconds,
                )
            ),
            dashboard_retention_days=int(
                env.get(
                    "PORTAL_DASHBOARD_RETENTION_DAYS",
                    defaults.dashboard_retention_days,
                )
            ),
            email_domain=_email_domain(
                env.get("PORTAL_EMAIL_DOMAIN"), defaults.email_domain
            ),
            user_signup_enabled=_env_bool(
                env.get("PORTAL_USER_SIGNUP_ENABLED"), defaults.user_signup_enabled
            ),
            signup_allowlist=_signup_allowlist(env.get("PORTAL_SIGNUP_ALLOWLIST")),
            email_delivery=_email_delivery(
                env.get("PORTAL_EMAIL_DELIVERY"), defaults.email_delivery
            ),
            email_send_timeout_seconds=float(
                env.get(
                    "PORTAL_EMAIL_SEND_TIMEOUT_SECONDS",
                    defaults.email_send_timeout_seconds,
                )
            ),
            email_code_ttl_seconds=int(
                env.get("PORTAL_EMAIL_CODE_TTL_SECONDS", defaults.email_code_ttl_seconds)
            ),
            email_code_length=int(
                env.get("PORTAL_EMAIL_CODE_LENGTH", defaults.email_code_length)
            ),
            email_code_max_attempts=int(
                env.get(
                    "PORTAL_EMAIL_CODE_MAX_ATTEMPTS", defaults.email_code_max_attempts
                )
            ),
            email_resend_cooldown_seconds=int(
                env.get(
                    "PORTAL_EMAIL_RESEND_COOLDOWN_SECONDS",
                    defaults.email_resend_cooldown_seconds,
                )
            ),
            email_send_window_seconds=int(
                env.get(
                    "PORTAL_EMAIL_SEND_WINDOW_SECONDS",
                    defaults.email_send_window_seconds,
                )
            ),
            email_max_sends_per_window=int(
                env.get(
                    "PORTAL_EMAIL_MAX_SENDS_PER_WINDOW",
                    defaults.email_max_sends_per_window,
                )
            ),
            email_max_failures_per_window=int(
                env.get(
                    "PORTAL_EMAIL_MAX_FAILURES_PER_WINDOW",
                    defaults.email_max_failures_per_window,
                )
            ),
            email_failure_window_seconds=int(
                env.get(
                    "PORTAL_EMAIL_FAILURE_WINDOW_SECONDS",
                    defaults.email_failure_window_seconds,
                )
            ),
            email_global_max_sends_per_hour=int(
                env.get(
                    "PORTAL_EMAIL_GLOBAL_MAX_SENDS_PER_HOUR",
                    defaults.email_global_max_sends_per_hour,
                )
            ),
            email_code_pepper=env.get("PORTAL_EMAIL_CODE_PEPPER") or None,
        )
