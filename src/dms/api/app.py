import os
from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import FileResponse, Response
from starlette.staticfiles import StaticFiles
from ..config import Settings
from ..db import Database
from ..repositories import Repositories
from ..wiring import build_execution_adapter, build_identity_resolver
from .routes_accounts import router as accounts_router
from .routes_auth import router as auth_router
from .routes_storages import router as storages_router, user_router as user_storages_router
from .routes_scan_paths import router as scan_paths_router
from .routes_requests import router as requests_router
from .routes_jobs import router as jobs_router
from .routes_artifacts import router as artifacts_router
from .routes_agent import router as agent_router
from .routes_nodes import router as nodes_router
from .routes_policies import router as policies_router
from .routes_denylist import router as denylist_router
from .routes_batches import router as batches_router
from .routes_control import router as control_router


def create_app(settings: Settings, db: Database) -> FastAPI:
    app = FastAPI(title="dms")
    app.state.settings = settings
    app.state.repos = Repositories(db)
    app.state.identity_resolver = build_identity_resolver(settings)
    app.state.execution_adapter = build_execution_adapter(settings, app.state.repos)
    app.add_middleware(SessionMiddleware, secret_key=settings.session_secret,
                       session_cookie="dms_session")

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    app.include_router(auth_router)
    app.include_router(accounts_router)
    app.include_router(storages_router)
    app.include_router(user_storages_router)
    app.include_router(scan_paths_router)
    app.include_router(requests_router)
    app.include_router(jobs_router)
    app.include_router(artifacts_router)
    app.include_router(agent_router)
    app.include_router(nodes_router)
    app.include_router(policies_router)
    app.include_router(denylist_router)
    app.include_router(batches_router)
    app.include_router(control_router)

    static_dir = settings.static_dir
    if static_dir and os.path.isdir(static_dir):
        static_root = os.path.abspath(static_dir)
        assets = os.path.join(static_root, "assets")
        if os.path.isdir(assets):
            app.mount("/assets", StaticFiles(directory=assets), name="assets")
        index_path = os.path.join(static_root, "index.html")

        @app.get("/{full_path:path}")
        def spa_fallback(full_path: str) -> Response:
            # /api·/healthz·/docs·/openapi.json 는 이미 위 라우터가 처리했다.
            candidate = os.path.normpath(os.path.join(static_root, full_path))
            if ((candidate == static_root or candidate.startswith(static_root + os.sep))
                    and os.path.isfile(candidate)):
                return FileResponse(candidate)
            return FileResponse(index_path)

    return app
