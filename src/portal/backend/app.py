"""FastAPI application factory for the portal BFF.

Serves the JSON auth API and, in production, the built Vite SPA as static assets.
In dev the SPA runs under the Vite dev server (which proxies ``/api`` here), so a
missing static dir is fine.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .auth import auth_router
from .config import DEV_DEFAULT_SESSION_SECRET, Settings
from .db import Database
from .dms_client import DmsClient
from .orchestrator import BackupOrchestrator
from .routers import operator_router, user_router
from .routers.backup import backup_router


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

    @app.on_event("startup")
    async def _startup() -> None:
        await app.state.db.open()
        if app.state.db.configured and settings.dms_configured:
            app.state.backup = BackupOrchestrator(
                app.state.db, app.state.dms_client, settings
            )
            app.state.backup.start()

    @app.on_event("shutdown")
    async def _shutdown() -> None:
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
        }

    app.include_router(auth_router(settings))
    # Role-separated interface APIs (each gated by role inside the router).
    app.include_router(user_router())
    app.include_router(operator_router())
    app.include_router(backup_router(settings))

    # Mount the SPA last so the API routes above take precedence. html=True
    # serves index.html for "/" and 404s fall through to it for client routing.
    static_dir = _static_dir()
    if static_dir is not None:
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="spa")
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
