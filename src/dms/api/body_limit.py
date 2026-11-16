"""요청 본문 상한 -- 순수 ASGI 미들웨어(2026-11-16 입구 교체: Envoy Gateway → 클러스터에 이미 깔린 Traefik).

왜 앱인가: Envoy BackendTrafficPolicy 의 requestBuffer.limit 20Mi(넘으면 입구가 413 -- 옛 Ingress proxy-body-size 20m 의
후신, 대량 배치 JSON 을 겨냥)가 하던 일을 대신한다. Traefik 은 본문 상한이 기본으로 없고, 상한을 주는 Buffering 미들웨어는
응답 버퍼를 끌 수 없어(포탈 전체 경로에 걸면 256MiB 결과 다운로드·내보내기 스트림이 Traefik 에 다 쌓인 뒤에야 나간다) 입구에
걸 수 없다. DMS 는 사이트의 Traefik 을 설치·변경하지도 않는다. 그래서 상한은 앱이 낸다:
settings.max_request_body_bytes(DMS_MAX_REQUEST_BODY_BYTES, 기본 20MiB = 옛 입구 값).

판정 -- 경계: 상한 바이트까지는 통과, 한 바이트라도 넘으면 413:
  1. Content-Length 가 상한을 넘으면 앱을 부르지 않고 곧바로 413 -- 본문을 한 바이트도 앱에 넘기지 않는다(인증·라우팅보다
     먼저다: 입구 상한과 같은 자리).
  2. 길이 머리가 없거나(chunked) 상한 이하로 선언됐어도 receive 를 감싸 받은 바이트를 센다 -- 넘는 순간 그 receive 가
     _BodyTooLarge 를 올리고 앱은 그 뒤의 본문을 받지 못한다(다 받아 둔 뒤 재면 그 사이 메모리를 상한 넘게 쓴다 --
     routes_agent.k8s_sync_result 와 같은 원칙). 앱이 그 예외를 자기 응답으로 바꿔도(FastAPI 는 본문 읽기 실패를 400 「There
     was an error parsing the body」로 바꾼다) 응답이 아직 안 나갔으면 413 으로 갈아 끼운다. 앱이 읽지 않는 본문은 세지 않는다
     (메모리를 쓰지 않는다 -- uvicorn 이 버퍼 수위(64KiB)에서 소켓 읽기를 멈추고, 응답 뒤 남은 본문은 버린다).
  응답 본문은 {"detail": "request_too_large"} -- 앱의 HTTPException 과 같은 모양이라 프런트 request() 가 detail 을 사유 코드로
  읽는다(reasonCodes.json·REASON_MESSAGES 양쪽 등록). 413 artifact_too_large(결과 파일 다운로드 **응답** 상한)와는 다른 코드다.

범위: 입구 경유뿐 아니라 dms-api 에 닿는 모든 HTTP 요청이다(클러스터 안 에이전트 포함 -- Envoy 때는 입구 경유만 걸렸다).
예외는 스스로 더 큰 상한을 재는 라우트뿐이다(_SELF_LIMITED_PATHS): 에이전트의 k8s 동기화 결과 POST 는 상한이 k8s_sync_max_bytes
+ 1MiB(기본 33MiB, 넘으면 413 k8s_sync_too_large)라 여기 걸면 20MiB~33MiB 인벤토리가 거꾸로 막힌다.

Connection: close 를 붙이지 않는 이유: 413 을 쓰고 곧바로 닫으면 아직 안 읽은 본문 때문에 커널이 RST 를 보내고, 프록시가
413 을 읽기 전에 RST 를 받으면 사용자는 413 대신 502 를 본다. uvicorn 은 응답을 끝낸 연결의 남은 본문을 쌓지 않고 버리며
읽은 뒤 연결을 다음 요청에 쓴다(h11_impl: our_state DONE 이면 Data 를 버린다).
"""
import re

from starlette.responses import JSONResponse

# 스스로 본문 상한을 재는 라우트 -- 이 미들웨어가 비켜선다. 라우트 모양 그대로(경로 매개변수 한 세그먼트) 전체 일치로 맞춘다:
# 접두로 열면 그 아래에 새 라우트가 생길 때 상한 없이 열린다. 각 라우트는 Content-Length 선검사 + 스트림 계수를 직접 한다.
#   /api/agent/k8s-sync/runs/{run_id} -- routes_agent.k8s_sync_result(상한 k8s_sync_max_bytes + _RESULT_ENVELOPE_BYTES).
_SELF_LIMITED_PATHS = (re.compile(r"/api/agent/k8s-sync/runs/[^/]+"),)


class _BodyTooLarge(Exception):
    """감싼 receive 가 상한을 넘는 순간 올린다 -- 이 미들웨어가 잡아 413 으로 바꾼다(밖으로 새지 않는다)."""


def too_large_response(*, detail: str) -> JSONResponse:
    # 사유 코드를 detail= 키워드 리터럴로 받는다: 사유 코드 추출기(tests/test_reason_codes_coverage.py)는 detail=/reason_code=
    # 키워드 리터럴만 읽는다 -- 부르는 쪽이 dict 리터럴({"detail": ...})로 쓰면 그물 밖이다.
    return JSONResponse(status_code=413, content={"detail": detail})


def self_limited(path: str) -> bool:
    return any(p.fullmatch(path) for p in _SELF_LIMITED_PATHS)


def declared_length(scope) -> "int | None":
    """Content-Length 값(바이트) -- 없거나 숫자가 아니면 None(모름 -- 스트림 계수가 맡는다). 서버(h11)가 서로 다른 중복
    값·틀린 값은 앱 앞에서 400 으로 끊는다."""
    for name, value in scope.get("headers") or ():
        if name == b"content-length":
            value = value.strip()
            return int(value) if value.isdigit() else None
    return None


class RequestBodyLimit:
    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or self_limited(scope.get("path") or ""):
            await self.app(scope, receive, send)
            return
        limit = self.max_bytes
        declared = declared_length(scope)
        if declared is not None and declared > limit:
            await too_large_response(detail="request_too_large")(scope, receive, send)
            return

        received = 0
        exceeded = False
        response_started = False   # 앱의 응답 머리가 나갔는가 -- 나간 뒤엔 413 으로 바꿀 수 없다
        replaced = False           # 앱 응답 대신 413 을 보냈는가 -- 그 뒤의 앱 메시지는 버린다

        async def limited_receive():
            nonlocal received, exceeded
            if exceeded:              # 예외를 삼키고 다시 읽으려는 앱에게도 남은 본문을 주지 않는다
                raise _BodyTooLarge()
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body") or b"")
                if received > limit:
                    exceeded = True
                    raise _BodyTooLarge()
            return message

        async def guarded_send(message):
            nonlocal response_started, replaced
            if replaced:
                return
            if message["type"] == "http.response.start" and not response_started:
                if exceeded:
                    replaced = True
                    await too_large_response(detail="request_too_large")(scope, receive, send)
                    return
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except Exception:
            # 본문을 끊은 뒤 앱이 올린 예외(_BodyTooLarge 그대로든 그 여파든)는 413 이다. 끊지 않았는데 난 예외, 그리고 응답을
            # 먼저 흘리며 본문을 읽던 앱(지금은 없다 -- 머리가 이미 나가 413 을 못 보낸다)의 예외는 그대로 올린다.
            if not exceeded or response_started:
                raise
        if exceeded and not replaced and not response_started:
            replaced = True
            await too_large_response(detail="request_too_large")(scope, receive, send)
