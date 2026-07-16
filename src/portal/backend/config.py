"""Portal BFF settings.

Config is env-driven under a ``PORTAL_*`` prefix (kept distinct from the DMS
backend's ``DMS_*`` so the two services never share config state). There is no
config file. Mirrors the DMS backend's ``Settings.from_env()`` convention.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os

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
    # 회사 AD 로그인이 아직 더미 스텁(auth.authenticate_ad)일 때 그 더미 경로를 허용할지.
    # 임시 개발용으로 기본 ON. 실제 AD 연동 후에는 false로 꺼서 더미 로그인을 차단한다.
    allow_dummy_ad: bool = True
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
            allow_dummy_ad=_env_bool(
                env.get("PORTAL_ALLOW_DUMMY_AD"), defaults.allow_dummy_ad
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
        )
