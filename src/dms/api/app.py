from fastapi import Depends, FastAPI
from starlette.middleware.sessions import SessionMiddleware
from ..config import Settings
from ..db import Database
from ..repositories import Repositories
from .auth import Identity, require_user


def create_app(settings: Settings, db: Database) -> FastAPI:
    app = FastAPI(title="dms")
    app.state.settings = settings
    app.state.repos = Repositories(db)
    app.add_middleware(SessionMiddleware, secret_key=settings.session_secret,
                       session_cookie="dms_session")

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/api/auth/me")
    def me(identity: Identity = Depends(require_user)):
        return {"actor": identity.actor, "role": identity.role}

    return app
