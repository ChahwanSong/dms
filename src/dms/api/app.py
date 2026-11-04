import os
import signal
import threading
import sys
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Match
from starlette.staticfiles import StaticFiles
from ..config import Settings
from ..db import Database, utc_now_iso
from ..repositories import Repositories
from ..wiring import (build_build_runner, build_execution_adapter,
                     build_node_lister, build_queue_reader,
                     build_rollout_runner, wire_reconnect_event)
from .login_guard import GlobalPacer, LoginAlarms, LoginGate
from .login_limiter import LoginRateLimiter
from .password_transport import PasswordTransport
from .routes_accounts import router as accounts_router
from .routes_auth import (router as auth_router, VERIFICATION_MAIL_CONCURRENCY,
                          VERIFICATION_MAIL_GLOBAL_LIMIT, VERIFICATION_MAIL_LIMIT,
                          VERIFICATION_MAIL_WINDOW_SECONDS)
from .routes_security import router as security_router
from .routes_mail_settings import router as mail_settings_router
from .routes_portal import public_router as portal_public_router, router as portal_router
from .mailer import SendThrottle
from .routes_storages import router as storages_router, user_router as user_storages_router
from .routes_scan_paths import router as scan_paths_router
from .routes_sync_pairs import router as sync_pairs_router, user_router as user_sync_pairs_router
from .routes_requests import router as requests_router
from .routes_request_purge import router as request_purge_router
from .routes_jobs import router as jobs_router
from .routes_artifacts import router as artifacts_router
from .routes_agent import router as agent_router
from .routes_k8s import router as k8s_router
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
from .routes_usage import ReportReadFailures, router as usage_router


class _SpaFallbackRoute(APIRoute):
    """SPA 폴백 catch-all -- `/api`·`/api/*` 에는 **매치 자체를 하지 않는다**(Match.NONE).

    본문에서 404 를 던지는 대신 매치에서 빼는 이유(2026-11-04 기능 점검 결함 #1): 라우터는 FULL 매치를 PARTIAL(메서드
    불일치)보다 먼저 집는다 -- catch-all 이 /api 를 FULL 로 잡으면 없는 경로는 200 index.html 이 됐고, PUT 전용 라우트의
    GET 도 405 대신 index.html 이 됐다. 매치에서 빠지면 FastAPI 기본 그대로 없는 경로 JSON 404(`{"detail":"Not Found"}`),
    다른 메서드 전용 경로 JSON 405(`Allow` 헤더)가 된다 -- 본문에서 404 를 던지면 405 가 404 로 뭉개진다. 끝 슬래시만 다른
    /api 경로도 JSON 404 다: 앱은 `redirect_slashes=False` 다(create_app). 켜 두면 매치에서 빠진 /api 끝 슬래시 경로가 처음으로
    Starlette 의 307 을 받는데, Location 이 scope 의 scheme 으로 만든 절대 URL 이라 TLS 엣지 뒤(uvicorn 이 X-Forwarded-Proto 를
    믿지 않는다)에선 `http://` 로 내려가고, 307 은 메서드·본문을 유지해 쓰기 본문이 평문으로 다시 간다. 비 API 경로는 이 라우트가
    FULL·PARTIAL 로 잡아 리다이렉트에 닿지 않는다. 판정은 이 라우트가 잘라 낸 `full_path` 로 한다(root_path 를 뺀 같은 경로 -- 위치 계산을 따로
    하지 않는다). 세그먼트로 가른다: `/apiary`·`/api-tokens` 는 SPA 몫."""

    def matches(self, scope):
        match, child_scope = super().matches(scope)
        if match != Match.NONE:
            full_path = child_scope.get("path_params", {}).get("full_path", "")
            if full_path == "api" or full_path.startswith("api/"):
                return Match.NONE, {}
        return match, child_scope


def create_app(settings: Settings, db: Database, exit_fn=None) -> FastAPI:
    # exit_fn: readyz 연속 실패 자기 종료의 실행부(슬라이스 22 §2.4). 기본은
    # 자신에게 SIGTERM -- uvicorn graceful 종료 -> 컨테이너 종료 -> restartPolicy
    # 재시작이다. 주입은 테스트용(실 SIGTERM 없이 발화를 검증한다).
    if exit_fn is None:
        def exit_fn():
            os.kill(os.getpid(), signal.SIGTERM)
    # redirect_slashes=False: 끝 슬래시 307 은 TLS 엣지 뒤에서 http:// 로 내려간다(_SpaFallbackRoute docstring) -- 퍼스트파티
    # 클라이언트는 끝 슬래시를 쓰지 않는다.
    app = FastAPI(title="dms", redirect_slashes=False)
    app.state.settings = settings
    app.state.repos = Repositories(db)
    app.state.execution_adapter = build_execution_adapter(settings, app.state.repos)
    app.state.build_runner = build_build_runner(settings, app.state.repos)
    # targets/same_tag 엔드포인트가 클러스터 현재 이미지를 읽기 전용으로 관찰하는 데
    # 쓴다(설계 §5/§7, 1f5f4e5) -- observe()만 쓰고 patch_image()는 호출하지 않는다.
    app.state.rollout_runner = build_rollout_runner(settings)
    # 슬라이스 17: /api/admin/metrics/queue 가 쓴다. 기본 백엔드(stub)에선
    # StubQueueReader 라 클러스터 없이도 라우트가 산다(설계 §2.5).
    app.state.queue_reader = build_queue_reader(settings)
    # 컨트롤 상태 no_proxy 힌트의 워커 노드 IP(2026-09-09) -- 실패는 힌트 생략.
    app.state.node_lister = build_node_lister(settings)
    # 슬라이스 22 §2.6: 재연결 성공의 영속 흔적(events.db_reconnected) 훅.
    wire_reconnect_event(db, app.state.repos)
    # 메모리 카운터(아래 로그인·인증 상한·경보)의 기준 시각 -- 보안 이벤트 화면이 「이 시각 이후 값, 재시작하면 비워짐」을
    # 보인다(2026-11-03).
    app.state.started_at = utc_now_iso()
    # 2026-09-07 웹 인증 하드닝: 로그인 감속기(프로세스 메모리, 2026-11-03 부터 아이디 키만)와 비밀번호 전송
    # 봉인기(세션 시크릿에서 유도한 P-256 키 -- 레플리카·재시작에 걸쳐 동일).
    app.state.login_limiter = LoginRateLimiter(
        settings.login_rate_limit_attempts, settings.login_rate_limit_window_seconds)
    # 2026-11-03 로그인·식별 묶음(api/login_guard.py, routes_auth.login 의 처리 순서): 아이디별 총 시도(성공 포함) 상한,
    # scrypt 대기형 슬롯, 대기형 전체 스위치(기본 꺼짐), 경보 카운터. 모두 프로세스 메모리.
    app.state.login_attempt_limiter = LoginRateLimiter(
        settings.login_id_attempt_limit, settings.login_id_attempt_window_seconds)
    app.state.login_gate = LoginGate(settings.login_scrypt_slots, settings.login_scrypt_max_wait_seconds)
    app.state.login_pacer = GlobalPacer(
        settings.login_global_pace_limit, settings.login_global_pace_window_seconds,
        settings.login_global_pace_max_wait_seconds)
    app.state.login_alarms = LoginAlarms(
        settings.login_alarm_window_seconds, settings.login_alarm_failures,
        settings.login_alarm_attempts, settings.login_alarm_source_failures)
    app.state.password_transport = PasswordTransport(settings.session_secret)
    # 2026-10-01 Knox 메일 릴레이: 수신자별 인증 메일 상한(프로세스 메모리 -- 릴레이 쪽 상한이 최후 방어선).
    # 클라이언트 IP 별 상한(mail_throttle_client)은 2026-11-03 에 지웠다(원 IP 는 기록·경보 전용).
    app.state.mail_throttle = SendThrottle(VERIFICATION_MAIL_LIMIT, VERIFICATION_MAIL_WINDOW_SECONDS)
    app.state.mail_throttle_global = SendThrottle(VERIFICATION_MAIL_GLOBAL_LIMIT,
                                                  VERIFICATION_MAIL_WINDOW_SECONDS)
    app.state.mail_send_slots = threading.BoundedSemaphore(VERIFICATION_MAIL_CONCURRENCY)
    # 두 상한을 "모두 통과할 때만 모두 기록"하는 peek~record 구간의 락(인증 메일·완료 안내 메일 공용), 그리고 거절
    # 이벤트를 (상한, 키)마다 창에 한 번만 남기는 기록표(routes_auth.request_verification_code).
    app.state.mail_throttle_lock = threading.Lock()
    app.state.mail_throttle_notes = SendThrottle(1, VERIFICATION_MAIL_WINDOW_SECONDS)
    # 인증번호 전체 오답 상한(routes_auth._consume_code, 키 "global" 하나 -- 2026-11-03 사용자 결정 1000회/1시간) --
    # 아이디별 누적 잠금(DB)과 별개 축. 옛 접속 IP 별 상한을 대체한다. 보안 이벤트 화면의 「전체 잠금 해제」가 비운다.
    app.state.verification_guess_limiter = LoginRateLimiter(
        settings.verification_guess_global_limit, settings.verification_guess_global_window_seconds)
    app.state.verification_guess_lock = threading.Lock()   # 검사 -> 소비 -> 기록을 원자로(_consume_code)
    if app.state.login_pacer.enabled:
        # 켜 둔 채 잊지 않게(2026-11-03): 스위치는 env + 재시작으로만 켜므로, 켜진 채 뜬 기동마다 한 줄을 남긴다(보안 이벤트
        # 화면). record_event 는 올리지 않는다(DB 가 아직 없어도 기동은 계속된다).
        app.state.repos.observability.record_event(
            component="auth", severity="info", event_type="login_pacing_enabled",
            message=(f"global login pacing is ON at startup: {settings.login_global_pace_limit} failures per "
                     f"{settings.login_global_pace_window_seconds}s, max wait "
                     f"{settings.login_global_pace_max_wait_seconds}s -- set DMS_LOGIN_GLOBAL_PACE_LIMIT=0 and "
                     "restart dms-api to turn it off"),
            payload={"limit": settings.login_global_pace_limit,
                     "window_seconds": settings.login_global_pace_window_seconds,
                     "max_wait_seconds": settings.login_global_pace_max_wait_seconds})
    # 2026-10-21 리뷰: 스토리지 합계의 리포트 읽기 실패 기억(프로세스 메모리, TTL) -- 같은 실패가 호출마다 읽기 예산을 먼저
    # 써서 뒤의 읽을 수 있는 대상에 닿지 못하는 것을 막는다(routes_usage.storage_totals 규칙 4).
    app.state.report_read_failures = ReportReadFailures()

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request, exc: RequestValidationError):
        # FastAPI 기본 422 는 오류 항목마다 `input`(본문 원문)을 되돌린다 -- 비밀번호
        # 필드가 실린 요청이 형식 오류를 내면 그 값이 응답으로 에코돼 프록시 응답
        # 로그에 남는다. 위치·종류·메시지만 남긴다(프런트 request() 는 detail 이
        # 문자열이 아니면 http_422 로 합성하므로 화면 계약 무영향).
        return JSONResponse(status_code=422, content={"detail": [
            {k: v for k, v in err.items() if k in ("type", "loc", "msg")}
            for err in exc.errors()]})
    # https_only(→ Secure 플래그): TLS 종단(ingress) 뒤 배포에서만 켠다 --
    # config.Settings.session_cookie_secure 주석 참고.
    app.add_middleware(SessionMiddleware, secret_key=settings.session_secret,
                       session_cookie="dms_session",
                       https_only=settings.session_cookie_secure)

    # async(2026-11-03): 동기 def 는 스레드풀(기본 40)에서 돈다 -- 로그인 폭주·릴레이 지연이 스레드를 잡아도 liveness 가
    # 그 줄에 서지 않고 이벤트 루프에서 바로 답한다(로그인은 슬롯을 이벤트 루프에서 기다린다, api/login_guard.py).
    @app.get("/healthz")
    async def healthz():
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
    app.include_router(security_router)
    app.include_router(storages_router)
    app.include_router(user_storages_router)
    app.include_router(scan_paths_router)
    app.include_router(sync_pairs_router)
    app.include_router(mail_settings_router)
    app.include_router(portal_public_router)
    app.include_router(portal_router)
    app.include_router(user_sync_pairs_router)
    app.include_router(requests_router)
    app.include_router(request_purge_router)
    app.include_router(jobs_router)
    app.include_router(artifacts_router)
    app.include_router(agent_router)
    app.include_router(k8s_router)
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
    app.include_router(usage_router)

    static_dir = settings.static_dir
    if static_dir and os.path.isdir(static_dir):
        static_root = os.path.abspath(static_dir)
        assets = os.path.join(static_root, "assets")
        if os.path.isdir(assets):
            app.mount("/assets", StaticFiles(directory=assets), name="assets")
        index_path = os.path.join(static_root, "index.html")

        def spa_fallback(full_path: str) -> Response:
            # /healthz·/readyz·/docs·/openapi.json 는 위에서 먼저 등록된 GET 이 FULL 로 이긴다. /api·/api/* 는
            # _SpaFallbackRoute 가 매치에서 뺀다 -- 여기까지 오지 않는다.
            candidate = os.path.normpath(os.path.join(static_root, full_path))
            if ((candidate == static_root or candidate.startswith(static_root + os.sep))
                    and os.path.isfile(candidate)):
                return FileResponse(candidate)
            return FileResponse(index_path)

        # app.get 은 라우트 클래스를 고를 수 없다 -- APIRouter.add_api_route 의 route_class_override 로 단다(같은 등록 경로).
        app.router.add_api_route("/{full_path:path}", spa_fallback, methods=["GET"],
                                 route_class_override=_SpaFallbackRoute)

    return app
