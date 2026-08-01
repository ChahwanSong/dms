"""FastAPI application factory for the portal BFF.

Serves the JSON auth API and, in production, the built Vite SPA as static assets.
In dev the SPA runs under the Vite dev server (which proxies ``/api`` here), so a
missing static dir is fine.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .auth import auth_router
from .config import (
    DEV_DEFAULT_SESSION_SECRET,
    EMAIL_DOMAIN_RE,
    PUBLIC_EMAIL_DOMAINS,
    Settings,
)
from .dashboard_sampler import DashboardSampler
from .db import Database
from .dms_client import DmsClient
from .orchestrator import BackupOrchestrator
from .routers import operator_router, user_router
from .routers.backup import backup_router
from .routers.dashboard import dashboard_router
from .routers.rmjob import rm_router
from .routers.scan import scan_router
from .routers.syncjob import sync_router
from .routers.user_scan import user_scan_router
from .routers.user_sync import user_sync_router
from .routers.voc import operator_voc_router, user_voc_router
from .rm_orchestrator import RmOrchestrator
from .scan_orchestrator import ScanOrchestrator
from .sync_orchestrator import SyncOrchestrator


def _static_dir() -> Path | None:
    """Resolve the built SPA directory, or None when not present (dev)."""
    override = os.environ.get("PORTAL_STATIC_DIR")
    candidates = [Path(override)] if override else []
    # Default container layout: assets copied next to the backend package.
    candidates.append(Path(__file__).resolve().parent / "static")
    for candidate in candidates:
        if candidate.is_dir() and (candidate / "index.html").is_file():
            return candidate
    return None


class SpaStaticFiles(StaticFiles):
    """Serve the SPA so redeploys take effect immediately: index.html is
    revalidated on every load (``no-cache``) while content-hashed assets are
    cached long-term (``immutable``). Without this, browsers heuristically cache
    index.html and keep loading a stale (old-hash) bundle after a new deploy."""

    async def get_response(self, path, scope):  # type: ignore[override]
        response = await super().get_response(path, scope)
        content_type = response.headers.get("content-type", "")
        if content_type.startswith("text/html"):
            response.headers["Cache-Control"] = "no-cache"
        elif path.startswith("assets"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    # Fail closed: refuse to boot with the dev-default cookie-signing secret
    # (known from source -> operator-session forgery) unless explicitly allowed.
    if (
        settings.session_secret == DEV_DEFAULT_SESSION_SECRET
        and not settings.allow_insecure_defaults
    ):
        raise RuntimeError(
            "PORTAL_SESSION_SECRET is unset/default. Set a strong "
            "PORTAL_SESSION_SECRET, or set PORTAL_ALLOW_INSECURE_DEFAULTS=1 for "
            "local development."
        )

    # The dev echo path prints verification codes to the server log. Refusing to boot
    # (rather than ignoring the flag) is deliberate: a silent no-op would show up only
    # as "why is no code appearing", while a failed rollout is caught immediately.
    if settings.email_dev_echo and not settings.allow_insecure_defaults:
        raise RuntimeError(
            "PORTAL_EMAIL_DELIVERY=log prints verification codes to the server log "
            "and is for development only. Set PORTAL_ALLOW_INSECURE_DEFAULTS=1 to "
            "acknowledge, or switch to PORTAL_EMAIL_DELIVERY=company (see "
            "install/portal-02-user-auth.md §5)."
        )

    # A malformed domain would silently mail codes to a domain nobody controls.
    # Empty is allowed (the feature is simply off).
    if settings.email_domain and not EMAIL_DOMAIN_RE.match(settings.email_domain):
        raise RuntimeError(
            f"PORTAL_EMAIL_DOMAIN is not a valid domain: {settings.email_domain!r}"
        )

    # Missing mail delivery must not CrashLoop the portal — it is the operator console
    # too, so the blast radius of failing closed at boot would be far worse than this
    # one feature being unavailable. Signup fails closed at request time instead (503).
    if settings.user_signup_enabled and not settings.email_configured:
        logging.getLogger("portal").warning(
            "user signup is enabled but email delivery is not usable "
            "(PORTAL_EMAIL_DELIVERY=%s, PORTAL_EMAIL_DOMAIN=%r) — code requests will 503",
            settings.email_delivery,
            settings.email_domain,
        )

    # An open signup gate on a PUBLIC mail domain means "anyone with an account at that
    # provider", and a signed-up user can run DMS data jobs. On a company domain the
    # domain itself is the membership proof, so an empty allowlist is fine there.
    if (
        settings.user_signup_enabled
        and settings.email_domain in PUBLIC_EMAIL_DOMAINS
        and not settings.signup_allowlist
    ):
        logging.getLogger("portal").warning(
            "PORTAL_EMAIL_DOMAIN=%s is a public mail provider and "
            "PORTAL_SIGNUP_ALLOWLIST is empty — anyone with an account there can "
            "self-register. Set PORTAL_SIGNUP_ALLOWLIST, or switch to the company domain.",
            settings.email_domain,
        )

    app = FastAPI(title="DMS Portal BFF")

    # Signed session cookie. https_only defaults on; the plain-HTTP testbed sets
    # PORTAL_SESSION_HTTPS_ONLY=false so the cookie rides over the NodePort.
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        session_cookie=settings.session_cookie,
        max_age=settings.session_max_age_seconds or None,
        same_site="lax",
        https_only=settings.session_https_only,
    )

    # Shared DMS HTTP client (the only thing that talks to DMS). Lives on
    # app.state; opened here, closed at shutdown.
    app.state.dms_client = DmsClient(settings)
    # Portal's own DB (login + data-backup) and the backup orchestrator. Opened
    # at startup (async); both are no-ops when PORTAL_DB_URL is unset.
    app.state.db = Database(settings)
    app.state.backup = None
    app.state.scan = None
    app.state.sync_orch = None
    app.state.rm_orch = None
    app.state.sampler = None

    @app.on_event("startup")
    async def _startup() -> None:
        await app.state.db.open()
        if app.state.db.configured and settings.dms_configured:
            app.state.backup = BackupOrchestrator(
                app.state.db, app.state.dms_client, settings
            )
            app.state.backup.start()
            app.state.scan = ScanOrchestrator(
                app.state.db, app.state.dms_client, settings
            )
            app.state.scan.start()
            app.state.sync_orch = SyncOrchestrator(
                app.state.db, app.state.dms_client, settings
            )
            app.state.sync_orch.start()
            app.state.rm_orch = RmOrchestrator(
                app.state.db, app.state.dms_client, settings
            )
            app.state.rm_orch.start()
            # Dashboard trend sampler: snapshots DMS work counts into the portal DB
            # (DMS has no history endpoint) to back the request/job time-series chart.
            app.state.sampler = DashboardSampler(
                app.state.db, app.state.dms_client, settings
            )
            app.state.sampler.start()

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        if app.state.sampler is not None:
            await app.state.sampler.stop()
        if app.state.rm_orch is not None:
            await app.state.rm_orch.stop()
        if app.state.sync_orch is not None:
            await app.state.sync_orch.stop()
        if app.state.scan is not None:
            await app.state.scan.stop()
        if app.state.backup is not None:
            await app.state.backup.stop()
        await app.state.db.close()
        await app.state.dms_client.aclose()

    @app.get("/healthz")
    def healthz() -> dict[str, object]:
        return {
            "status": "ok",
            "dms_configured": settings.dms_configured,
            "db_configured": settings.db_configured,
            # Not secret. NOTE: these reflect Deployment env only (domain + provider) —
            # they say nothing about whether a delivery credential in the Secret is
            # real or still a placeholder, so do not use them to check Secret
            # re-injection after `kubectl apply`.
            "email_configured": settings.email_configured,
            "email_domain": settings.email_domain,
            "cluster_name": settings.cluster_name,
        }

    @app.get("/api/config")
    def app_config() -> dict[str, object]:
        """Public, pre-auth app chrome config.

        The SPA needs the cluster name on the login screen too (before any session
        exists), so this cannot live behind auth or on /api/auth/me. Deliberately
        holds only non-secret display values.
        """
        return {"cluster_name": settings.cluster_name}

    app.include_router(auth_router(settings))
    # Role-separated interface APIs (each gated by role inside the router).
    app.include_router(user_router())
    app.include_router(user_sync_router(settings))
    app.include_router(user_scan_router(settings))
    app.include_router(user_voc_router())
    app.include_router(operator_router())
    app.include_router(operator_voc_router())
    app.include_router(backup_router(settings))
    app.include_router(scan_router(settings))
    app.include_router(sync_router(settings))
    app.include_router(rm_router(settings))
    app.include_router(dashboard_router(settings))

    # Mount the SPA last so the API routes above take precedence. html=True
    # serves index.html for "/" and 404s fall through to it for client routing.
    static_dir = _static_dir()
    if static_dir is not None:
        app.mount("/", SpaStaticFiles(directory=static_dir, html=True), name="spa")
    else:

        @app.get("/")
        def root() -> JSONResponse:
            return JSONResponse(
                {
                    "service": "dms-portal-bff",
                    "spa": "not built — run the Vite dev server or build the image",
                }
            )

    return app
