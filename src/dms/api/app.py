import os
import signal
import sys
from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.staticfiles import StaticFiles
from ..config import Settings
from ..db import Database
from ..repositories import Repositories
from ..wiring import (build_build_runner, build_execution_adapter,
                     build_identity_resolver, build_queue_reader,
                     build_rollout_runner, wire_reconnect_event)
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
from .routes_artifact_base import router as artifact_base_router
from .routes_control import router as control_router
from .routes_builds import router as builds_router
from .routes_registry import router as registry_router
from .routes_releases import router as releases_router
from .routes_metrics import router as metrics_router


def create_app(settings: Settings, db: Database, exit_fn=None) -> FastAPI:
    # exit_fn: readyz 연속 실패 자기 종료의 실행부(슬라이스 22 §2.4). 기본은
    # 자신에게 SIGTERM -- uvicorn graceful 종료 -> 컨테이너 종료 -> restartPolicy
    # 재시작이다. 주입은 테스트용(실 SIGTERM 없이 발화를 검증한다).
    if exit_fn is None:
        def exit_fn():
            os.kill(os.getpid(), signal.SIGTERM)
    app = FastAPI(title="dms")
    app.state.settings = settings
    app.state.repos = Repositories(db)
    app.state.identity_resolver = build_identity_resolver(settings)
    app.state.execution_adapter = build_execution_adapter(settings, app.state.repos)
    app.state.build_runner = build_build_runner(settings)
    # targets/same_tag 엔드포인트가 클러스터 현재 이미지를 읽기 전용으로 관찰하는 데
    # 쓴다(설계 §5/§7, 1f5f4e5) -- observe()만 쓰고 patch_image()는 호출하지 않는다.
    app.state.rollout_runner = build_rollout_runner(settings)
    # 슬라이스 17: /api/admin/metrics/queue 가 쓴다. 기본 백엔드(stub)에선
    # StubQueueReader 라 클러스터 없이도 라우트가 산다(설계 §2.5).
    app.state.queue_reader = build_queue_reader(settings)
    # 슬라이스 22 §2.6: 재연결 성공의 영속 흔적(events.db_reconnected) 훅.
    wire_reconnect_event(db, app.state.repos)
    app.add_middleware(SessionMiddleware, secret_key=settings.session_secret,
                       session_cookie="dms_session")

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    # 연속 readyz 실패 카운터(슬라이스 22 §2.4). 리스트에 담는 이유는 클로저에서
    # 재바인딩하기 위함이고, 요청 처리가 단일 커넥션 RLock 뒤에서 직렬화되므로
    # 별도 락이 필요 없다.
    _readyz_fails = [0]

    @app.get("/readyz")
    def readyz():
        # liveness(/healthz)와 달리 실제 의존성(DB)에 쿼리를 날려 readiness를 검증한다 —
        # DB가 죽으면 인증 게이트를 포함한 거의 모든 요청이 500이 되므로, readiness
        # probe가 이를 감지해 Service에서 이 파드를 빼야 한다.
        try:
            db.query_one("SELECT 1 AS x")
        except Exception:
            _readyz_fails[0] += 1
            limit = settings.readyz_exit_failures
            # 재연결(§2.2)이 들어간 뒤의 readyz 실패는 "**재연결까지 실패**"를
            # 뜻한다. 그 상태로 임계(기본 30회 ≈ 5분)를 넘기면 파드 교체가 옳다 --
            # 이미 readiness 503 으로 Service 에서 빠져 있어 종료로 잃는 가용성이
            # 0 이고, 2026-08-11 사건의 실측된 유일한 처방이 "파드 삭제"였다.
            if limit > 0 and _readyz_fails[0] >= limit:
                print(f"readyz failed {_readyz_fails[0]} times in a row "
                      f"(limit={limit}) -- self-terminating", file=sys.stderr)
                exit_fn()
            # 503 본문은 기존 그대로 -- 프로브 계약을 건드리지 않는다.
            return JSONResponse(status_code=503, content={"status": "degraded"})
        _readyz_fails[0] = 0   # **연속** 실패만 센다 -- 성공 한 번이면 리셋
        # kubelet 은 상태 코드만 본다 -- 본문 카운터는 운영자용이다(curl 한 번으로
        # 재연결 빈도를 본다). 조용한 재연결을 만들지 않기 위한 창구(§2.6).
        return {"status": "ok", "reconnects": db.reconnect_count,
                "last_reconnect_at": db.last_reconnect_at}

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
    app.include_router(artifact_base_router)
    app.include_router(builds_router)
    app.include_router(registry_router)
    app.include_router(releases_router)
    app.include_router(metrics_router)

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
