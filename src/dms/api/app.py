from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware
from ..config import Settings
from ..db import Database
from ..repositories import Repositories
from .routes_auth import router as auth_router
from .routes_storages import router as storages_router


def create_app(settings: Settings, db: Database) -> FastAPI:
    app = FastAPI(title="dms")
    app.state.settings = settings
    app.state.repos = Repositories(db)
    app.add_middleware(SessionMiddleware, secret_key=settings.session_secret,
                       session_cookie="dms_session")

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    app.include_router(auth_router)
    app.include_router(storages_router)

    return app
