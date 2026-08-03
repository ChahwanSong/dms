from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware
from ..config import Settings
from ..db import Database
from ..repositories import Repositories
from .routes_auth import router as auth_router
from .routes_storages import router as storages_router
from .routes_requests import router as requests_router
from .routes_agent import router as agent_router
from .routes_nodes import router as nodes_router
from .routes_policies import router as policies_router


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
    app.include_router(requests_router)
    app.include_router(agent_router)
    app.include_router(nodes_router)
    app.include_router(policies_router)

    return app
